"""Phase 1b file-protocol client for mission issue/cancel/status operations.

The client owns the Python-side command envelope and revision. The engine is
the sole writer of ``mission-status.json`` and ``mission-audit.jsonl``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class MissionProtocolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ACTOR_ID_RE = re.compile(r"^(?:enemy|own)_[a-z0-9]+(?:_[a-z0-9]+)*$")
_POI_ID_RE = re.compile(r"^(?:oil|hospital|tech|base)_[a-z0-9]+(?:_[a-z0-9]+)*$")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class MissionClient:
    """Issue and inspect missions for one session directory."""

    def __init__(self, session_id: str, runtime_root: str | os.PathLike[str], fsync: bool = False):
        if not _ID_RE.fullmatch(session_id):
            raise MissionProtocolError("SESSION_MISMATCH", "invalid session_id")
        self.session_id = session_id
        self.session_dir = Path(runtime_root) / session_id
        self.commands_path = self.session_dir / "mission-commands.jsonl"
        self.status_path = self.session_dir / "mission-status.json"
        self.audit_path = self.session_dir / "mission-audit.jsonl"
        self.fsync = fsync
        self._lock = _lock_for(self.commands_path)

    def issue_mission(self, mission: dict[str, Any], observed_tick: int = 0) -> dict[str, Any]:
        normalized = dict(mission)
        normalized.setdefault("id", f"m_{uuid.uuid4().hex[:12]}")
        self._validate_mission(normalized)
        with self._lock:
            existing = self._find_existing_issue(normalized)
            if existing is not None:
                return existing
            event = {
                "schema_version": 1,
                "session_id": self.session_id,
                "revision": self._next_revision(),
                "op": "issue",
                "observed_tick": max(0, int(observed_tick)),
                "mission": normalized,
            }
            self._append_event(event)
        return event

    def _find_existing_issue(self, mission: dict[str, Any]) -> dict[str, Any] | None:
        if not self.commands_path.exists():
            return None
        text = self.commands_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not text.endswith("\n"):
                    continue
                raise MissionProtocolError("INVALID_SCHEMA", "mission command log contains malformed JSON")
            if event.get("op") != "issue" or event.get("mission", {}).get("id") != mission.get("id"):
                continue
            if event.get("mission") == mission:
                return event
            raise MissionProtocolError("MISSION_CONFLICT", "mission id already exists with a different payload")
        return None

    def cancel_mission(self, mission_id: str, reason: str = "operator_cancel") -> dict[str, Any]:
        if not _ID_RE.fullmatch(mission_id):
            raise MissionProtocolError("INVALID_SCHEMA", "invalid mission_id")
        with self._lock:
            # Cancellation is idempotent across MCP retries and process-local
            # reconnects: return the already-persisted envelope instead of
            # advancing the command revision a second time.
            existing = self._find_existing_cancel(mission_id)
            if existing is not None:
                return existing
            event = {
                "schema_version": 1,
                "session_id": self.session_id,
                "revision": self._next_revision(),
                "op": "cancel",
                "mission_id": mission_id,
                "reason": reason[:200],
            }
            self._append_event(event)
        return event

    def _find_existing_cancel(self, mission_id: str) -> dict[str, Any] | None:
        if not self.commands_path.exists():
            return None
        latest: dict[str, Any] | None = None
        text = self.commands_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not text.endswith("\n"):
                    continue
                raise MissionProtocolError("INVALID_SCHEMA", "mission command log contains malformed JSON")
            if event.get("op") == "cancel" and event.get("mission_id") == mission_id:
                latest = event
        return latest

    def read_missions(self, mission_id: str | None = None, retries: int = 3) -> dict[str, Any]:
        last_error: Exception | None = None
        for _ in range(max(1, retries)):
            try:
                status = json.loads(self.status_path.read_text(encoding="utf-8"))
                if status.get("schema_version") != 1:
                    raise MissionProtocolError("INVALID_SCHEMA", "unsupported mission status schema")
                if status.get("session_id") != self.session_id:
                    raise MissionProtocolError("SESSION_MISMATCH", "status belongs to another session")
                if mission_id is not None:
                    matches = [m for m in status.get("missions", []) if m.get("id") == mission_id]
                    status["missions"] = matches
                return status
            except MissionProtocolError:
                raise
            except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError) as exc:
                last_error = exc
                time.sleep(0.02)
        raise MissionProtocolError("STATUS_UNAVAILABLE", f"mission status is unavailable: {last_error}")

    def wait_for_terminal(self, mission_id: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.read_missions(mission_id)
            mission = status.get("missions", [None])[0]
            if mission and mission.get("status") in {"succeeded", "failed", "cancelled"}:
                return mission
            time.sleep(0.1)
        raise MissionProtocolError("STATUS_UNAVAILABLE", f"mission {mission_id} did not reach terminal state")

    def _next_revision(self) -> int:
        with self._lock:
            if not self.commands_path.exists():
                return 1
            text = self.commands_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            revisions = []
            for index, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    # A torn final line is recoverable; a malformed interior
                    # line must not be silently skipped.
                    if index == len(lines) - 1 and not text.endswith("\n"):
                        continue
                    raise MissionProtocolError("INVALID_SCHEMA", "mission command log contains malformed JSON")
                revisions.append(int(value.get("revision", 0)))
            return max(revisions, default=0) + 1

    def _append_event(self, event: dict[str, Any]) -> None:
        payload = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.commands_path.open("ab") as stream:
                stream.write(payload)
                stream.flush()
                if self.fsync:
                    os.fsync(stream.fileno())

    @staticmethod
    def _validate_mission(mission: dict[str, Any]) -> None:
        if not isinstance(mission, dict):
            raise MissionProtocolError("INVALID_SCHEMA", "mission must be an object")
        required = {"id", "type", "priority", "ttl_ticks"}
        missing = required - mission.keys()
        if missing:
            raise MissionProtocolError("INVALID_SCHEMA", f"missing mission fields: {sorted(missing)}")
        if not _ID_RE.fullmatch(str(mission["id"])):
            raise MissionProtocolError("INVALID_SCHEMA", "invalid mission id")
        common = {"id", "type", "priority", "ttl_ticks", "notes"}
        allowed_by_type = {
            "attack": common | {"target", "force_units"},
            "capture": common | {"target", "unit_type", "escort_units"},
            "produce": common | {"actor_type", "count"},
            "build": common | {"actor_type", "count"},
        }
        mission_type = mission.get("type")
        if mission_type in allowed_by_type:
            unknown = set(mission) - allowed_by_type[mission_type]
            if unknown:
                raise MissionProtocolError("INVALID_SCHEMA", f"unknown mission fields: {sorted(unknown)}")
        if mission["type"] not in {"attack", "capture", "produce", "build"}:
            raise MissionProtocolError("MISSION_TYPE_NOT_ENABLED", "mission type is not enabled in Phase 1b")
        if mission["priority"] not in {"low", "medium", "high"}:
            raise MissionProtocolError("INVALID_SCHEMA", "invalid mission priority")
        ttl = mission["ttl_ticks"]
        if not isinstance(ttl, int) or not 25 <= ttl <= 45000:
            raise MissionProtocolError("INVALID_SCHEMA", "ttl_ticks must be between 25 and 45000")
        mission_type = mission["type"]
        if mission_type == "attack" and not _ACTOR_ID_RE.fullmatch(str(mission.get("target", ""))):
            raise MissionProtocolError("INVALID_SCHEMA", "attack target must be an ActorSemanticId")
        if mission_type == "capture":
            if not _POI_ID_RE.fullmatch(str(mission.get("target", ""))):
                raise MissionProtocolError("INVALID_SCHEMA", "capture target must be a POI semantic id")
            if not re.fullmatch(r"^[a-z0-9._-]+$", str(mission.get("unit_type", ""))):
                raise MissionProtocolError("INVALID_SCHEMA", "capture unit_type is invalid")
        if mission_type in {"produce", "build"}:
            if not re.fullmatch(r"^[a-z0-9._-]+$", str(mission.get("actor_type", ""))):
                raise MissionProtocolError("INVALID_SCHEMA", "actor_type is invalid")
            if not isinstance(mission.get("count"), int) or mission["count"] < 1:
                raise MissionProtocolError("INVALID_SCHEMA", "count must be positive")
