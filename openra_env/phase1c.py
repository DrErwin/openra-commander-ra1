"""Phase 1c semantic observation and MCP façade.

This module is deliberately a small, dependency-light boundary between the
raw OpenRA bridge and an Agent.  Raw protobuf fields (including actor ids) are
kept inside the process; the five public operations return only semantic ids,
Agenda names and structured errors.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from openra_env.server.mission_client import MissionClient, MissionProtocolError


SCHEMA_VERSION = 1
STALE_AFTER_MS = 2_000
DISCONNECTED_AFTER_MS = 5_000
ALERT_RETENTION = 2_048
ALERT_TERMINAL_RETENTION_SECONDS = 30 * 60
CURSOR_RE = re.compile(r"^v1:([A-Za-z0-9][A-Za-z0-9_-]{0,63}):(\d+)$")
SEMANTIC_RE = re.compile(r"^(?:own|enemy)_[a-z0-9]+(?:_[a-z0-9]+)*$")


AGENDA_ZONES: dict[str, dict[str, int]] = {
    "west_lane": {"x_min": 1, "x_max": 47, "y_min": 1, "y_max": 72},
    "north_center": {"x_min": 48, "x_max": 80, "y_min": 1, "y_max": 27},
    "center": {"x_min": 48, "x_max": 80, "y_min": 28, "y_max": 49},
    "south_center": {"x_min": 48, "x_max": 80, "y_min": 50, "y_max": 72},
    "east_lane": {"x_min": 81, "x_max": 128, "y_min": 1, "y_max": 72},
}

AGENDA_POIS: dict[str, dict[str, Any]] = {
    "oil_west_edge": {"kind": "oil", "x": 5, "y": 33, "actor_type": "oilb"},
    "oil_north_west": {"kind": "oil", "x": 60, "y": 20, "actor_type": "oilb"},
    "oil_center_west": {"kind": "oil", "x": 57, "y": 36, "actor_type": "oilb"},
    "oil_north_east": {"kind": "oil", "x": 68, "y": 20, "actor_type": "oilb"},
    "oil_center_east": {"kind": "oil", "x": 71, "y": 36, "actor_type": "oilb"},
    "oil_east_edge": {"kind": "oil", "x": 123, "y": 33, "actor_type": "oilb"},
    "hospital_center": {"kind": "hospital", "x": 64, "y": 33, "actor_type": "hosp"},
    "hospital_south": {"kind": "hospital", "x": 64, "y": 68, "actor_type": "hosp"},
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _zone(x: int | float, y: int | float) -> str | None:
    for name, bounds in AGENDA_ZONES.items():
        if bounds["x_min"] <= x <= bounds["x_max"] and bounds["y_min"] <= y <= bounds["y_max"]:
            return name
    return None


def _distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    return math.hypot(float(a.get("cell_x", a.get("pos_x", 0))) - float(b.get("cell_x", b.get("pos_x", 0))),
                      float(a.get("cell_y", a.get("pos_y", 0))) - float(b.get("cell_y", b.get("pos_y", 0))))


def _envelope(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "schema_version": SCHEMA_VERSION}


def _error(code: str, message: str, retryable: bool = False, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable, "details": dict(details or {})},
        "schema_version": SCHEMA_VERSION,
    }


def discover_session_id(runtime_root: str | os.PathLike[str] | None = None) -> str | None:
    """Discover exactly one runtime session for MCP restart recovery.

    An empty result or more than one candidate is intentionally ambiguous; it
    never silently selects the first directory.
    """
    root = Path(runtime_root or os.environ.get("RL_MISSION_DIR") or os.environ.get("RL_OBSERVATION_DIR") or ".")
    if not root.exists():
        return None
    candidates = []
    for directory in root.iterdir():
        if not directory.is_dir() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", directory.name):
            continue
        if any((directory / name).exists() for name in ("mission-status.json", "mission-commands.jsonl", "latest-observation.json")):
            candidates.append(directory.name)
    return candidates[0] if len(candidates) == 1 else None


@dataclass
class ObservationFrame:
    raw: dict[str, Any]
    received_at_ms: int
    sequence: int
    observed_at_unix_ms: int
    source: str = "grpc"

    @property
    def age_ms(self) -> int:
        return max(0, _now_ms() - self.observed_at_unix_ms)

    @property
    def stale(self) -> bool:
        return self.age_ms > STALE_AFTER_MS

    @property
    def disconnected(self) -> bool:
        return self.age_ms > DISCONNECTED_AFTER_MS


class ObservationCache:
    """Session-scoped latest-frame cache with monotonic sequence checks."""

    def __init__(self, session_id: str, max_age_ms: int = STALE_AFTER_MS):
        self.session_id = session_id
        self.max_age_ms = max_age_ms
        self.frame: ObservationFrame | None = None
        self.last_error: str | None = None

    def update(self, raw: Mapping[str, Any], *, received_at_ms: int | None = None, source: str = "grpc") -> ObservationFrame:
        raw = dict(raw)
        sid = str(raw.get("episode_id") or raw.get("session_id") or self.session_id)
        if sid != self.session_id:
            raise ValueError("session_mismatch")
        sequence = int(raw.get("observation_sequence", raw.get("sequence", 0)))
        observed = int(raw.get("observed_at_unix_ms", raw.get("observed_at", _now_ms())))
        if self.frame is not None and sequence < self.frame.sequence:
            raise ValueError("sequence_regressed")
        self.frame = ObservationFrame(raw, received_at_ms or _now_ms(), sequence, observed, source)
        self.last_error = None
        return self.frame

    def read(self, *, require_fresh: bool = True) -> ObservationFrame:
        if self.frame is None:
            raise RuntimeError("observation_disconnected")
        if self.frame.disconnected:
            self.last_error = "observation_disconnected"
            raise RuntimeError("observation_disconnected")
        if require_fresh and self.frame.stale:
            self.last_error = "observation_stale"
            raise RuntimeError("observation_stale")
        return self.frame


@dataclass
class SemanticIdentity:
    semantic_id: str
    owner: str
    actor_type: str
    actor_id: int
    first_seen_tick: int
    last_seen_tick: int
    last_zone: str | None
    confidence: float = 1.0
    stale: bool = False
    targetable: bool = True
    building: bool = False
    tombstone: bool = False


class SemanticRegistry:
    """Stable semantic names for a session, with fog-aware enemy identities."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._by_actor: dict[tuple[str, int], SemanticIdentity] = {}
        self._by_semantic: dict[str, SemanticIdentity] = {}
        self._next_ordinal: dict[tuple[str, str, str], int] = {}

    def _new_name(self, owner: str, actor_type: str, zone: str | None) -> str:
        prefix = "own" if owner.lower() in {"multi1", "agent", "player", "russia", "self", "own"} else "enemy"
        slug = re.sub(r"[^a-z0-9]+", "_", actor_type.lower()).strip("_") or "actor"
        zone_slug = (zone or "unknown").lower()
        key = (prefix, slug, zone_slug if prefix == "enemy" else "")
        ordinal = self._next_ordinal.get(key, 0) + 1
        self._next_ordinal[key] = ordinal
        if prefix == "enemy":
            return f"enemy_{slug}_{zone_slug}_{ordinal}"
        return f"own_{slug}_{ordinal}"

    def observe(self, actors: Iterable[Mapping[str, Any]], tick: int, *, enemy: bool = False) -> list[dict[str, Any]]:
        seen: set[tuple[str, int]] = set()
        output: list[dict[str, Any]] = []
        for actor in actors:
            actor_id = int(actor.get("actor_id", 0))
            owner = str(actor.get("owner", "enemy" if enemy else "own"))
            actor_type = str(actor.get("type", "unknown"))
            x, y = int(actor.get("cell_x", actor.get("pos_x", 0))), int(actor.get("cell_y", actor.get("pos_y", 0)))
            zone = _zone(x, y)
            key = (owner, actor_id)
            identity = self._by_actor.get(key)
            if identity is None:
                identity = SemanticIdentity(self._new_name(owner, actor_type, zone), owner, actor_type, actor_id, tick, tick, zone,
                                            building=bool(actor.get("is_building", False)))
                self._by_actor[key] = identity
                self._by_semantic[identity.semantic_id] = identity
            identity.last_seen_tick = tick
            identity.last_zone = zone or identity.last_zone
            identity.confidence = 1.0
            identity.stale = False
            identity.targetable = True
            seen.add(key)
            item = {k: v for k, v in actor.items() if k not in {"actor_id", "owner"}}
            item.update({"semantic_id": identity.semantic_id, "actor_type": actor_type, "owner_side": "own" if identity.semantic_id.startswith("own_") else "enemy", "zone": zone,
                         "confidence": identity.confidence, "last_seen_tick": tick, "stale": False,
                         "targetable": True})
            output.append(item)

        # Decay only enemy identities; own units are always authoritative.
        for key, identity in self._by_actor.items():
            if key in seen or identity.owner.lower() in {"multi1", "agent", "player", "russia", "self", "own"}:
                continue
            age = max(0, tick - identity.last_seen_tick)
            threshold = 1500 if identity.building else 250
            identity.confidence = max(0.0, 1.0 - age / float(threshold))
            identity.stale = age >= threshold
            identity.targetable = not identity.stale
        return output

    def targetable(self, semantic_id: str) -> SemanticIdentity | None:
        if not SEMANTIC_RE.fullmatch(semantic_id):
            return None
        identity = self._by_semantic.get(semantic_id)
        return identity if identity and identity.targetable and not identity.tombstone else None

    def mark_destroyed(self, actor_id: int) -> None:
        for identity in self._by_actor.values():
            if identity.actor_id == int(actor_id):
                identity.tombstone = True
                identity.stale = True
                identity.targetable = False
                identity.confidence = 0.0


class AlertStore:
    """Append-only alert ring with opaque, session-bound cursors."""

    def __init__(self, session_id: str, retention: int = ALERT_RETENTION):
        self.session_id = session_id
        self.retention = max(1, retention)
        self._alerts: deque[dict[str, Any]] = deque(maxlen=self.retention)
        self._sequence = 0
        self._last_dedupe: dict[str, int] = {}
        self._created_at_ms: dict[int, int] = {}

    def _purge(self) -> None:
        cutoff = _now_ms() - ALERT_TERMINAL_RETENTION_SECONDS * 1000
        while self._alerts and self._created_at_ms.get(self._alerts[0]["sequence"], _now_ms()) < cutoff:
            removed = self._alerts.popleft()
            self._created_at_ms.pop(removed["sequence"], None)

    @property
    def earliest_sequence(self) -> int:
        return self._alerts[0]["sequence"] if self._alerts else self._sequence + 1

    @property
    def cursor(self) -> str:
        return f"v1:{self.session_id}:{self._sequence}"

    def append(self, alert: Mapping[str, Any], *, dedupe_key: str | None = None, tick: int = 0) -> dict[str, Any]:
        self._purge()
        if dedupe_key and tick - self._last_dedupe.get(dedupe_key, -10**9) < 25:
            return dict(self._alerts[-1]) if self._alerts else {}
        self._sequence += 1
        item = {
            "id": str(alert.get("id") or f"alert_{uuid.uuid4().hex[:12]}"),
            "sequence": self._sequence,
            "tick": max(0, int(alert.get("tick", tick))),
            "source": str(alert.get("source", "semantic")),
            "type": str(alert.get("type", "zone_threat")),
            "severity": str(alert.get("severity", "warning")),
            "message": str(alert.get("message", ""))[:500] or "alert",
            "zone": alert.get("zone"),
            "subject_semantic_id": alert.get("subject_semantic_id"),
            "details": dict(alert.get("details") or {}),
        }
        self._alerts.append(item)
        self._created_at_ms[item["sequence"]] = _now_ms()
        if dedupe_key:
            self._last_dedupe[dedupe_key] = item["tick"]
        return item

    def read(self, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
        self._purge()
        limit = max(1, min(100, int(limit)))
        if cursor is not None:
            match = CURSOR_RE.fullmatch(cursor)
            if not match:
                return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id, "previous_cursor": cursor, "cursor": self.cursor,
                        "generated_at_tick": self._alerts[-1]["tick"] if self._alerts else 0,
                        "cursor_expired": True, "session_mismatch": False, "alerts": []}
            sid, sequence_text = match.groups()
            if sid != self.session_id:
                return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id, "previous_cursor": cursor, "cursor": self.cursor,
                        "generated_at_tick": self._alerts[-1]["tick"] if self._alerts else 0,
                        "cursor_expired": False, "session_mismatch": True, "alerts": []}
            sequence = int(sequence_text)
            if sequence < self.earliest_sequence - 1:
                return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id, "previous_cursor": cursor, "cursor": self.cursor,
                        "generated_at_tick": self._alerts[-1]["tick"] if self._alerts else 0,
                        "cursor_expired": True, "session_mismatch": False, "alerts": []}
            selected = [item for item in self._alerts if item["sequence"] > sequence][:limit]
        else:
            selected = list(self._alerts)[-limit:]
        current = f"v1:{self.session_id}:{selected[-1]['sequence']}" if selected else self.cursor
        return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id, "previous_cursor": cursor, "cursor": current,
                "generated_at_tick": selected[-1]["tick"] if selected else (self._alerts[-1]["tick"] if self._alerts else 0),
                "cursor_expired": False, "session_mismatch": False, "alerts": selected}


class SemanticAggregator:
    """Converts raw frames and sidecar engine events into the Agent view."""

    def __init__(self, session_id: str, *, alert_retention: int = ALERT_RETENTION):
        self.session_id = session_id
        self.registry = SemanticRegistry(session_id)
        self.alerts = AlertStore(session_id, alert_retention)
        self.cache = ObservationCache(session_id)
        self._previous_units: list[dict[str, Any]] = []
        self._previous_enemies: list[dict[str, Any]] = []
        self._previous_power_deficit = False
        self._last_battlefield: dict[str, Any] | None = None

    def ingest(self, raw: Mapping[str, Any], *, source: str = "grpc", low_level_events: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
        frame = self.cache.update(raw, source=source)
        data = frame.raw
        tick = int(data.get("tick", 0))
        units = self.registry.observe(data.get("units", []), tick, enemy=False)
        buildings = self.registry.observe(data.get("buildings", []), tick, enemy=False)
        enemies = self.registry.observe(data.get("visible_enemies", []), tick, enemy=True)
        enemy_buildings = self.registry.observe(data.get("visible_enemy_buildings", []), tick, enemy=True)
        own_by_type: dict[str, int] = {}
        for item in units:
            own_by_type[item["actor_type"]] = own_by_type.get(item["actor_type"], 0) + 1
        clusters: list[dict[str, Any]] = []
        for item in enemies:
            zone = item.get("zone") or "unknown"
            cluster = next((c for c in clusters if c["zone"] == zone and _distance(c["center"], item) <= 8), None)
            if cluster is None:
                cluster = {"zone": zone, "center": {"cell_x": item.get("cell_x", 0), "cell_y": item.get("cell_y", 0)}, "count": 0,
                           "power": 0, "members": []}
                clusters.append(cluster)
            cluster["count"] += 1
            cluster["power"] += 1 if item.get("can_attack") else 0
            cluster["members"].append(item["semantic_id"])

        economy = data.get("economy", {})
        power_provided = int(economy.get("power_provided", 0))
        power_drained = int(economy.get("power_drained", 0))
        battlefield = {
            "schema_version": SCHEMA_VERSION, "session_id": self.session_id, "tick": tick,
            "stale": frame.stale, "economy": {
                "cash": int(economy.get("cash", 0)), "ore": int(economy.get("ore", 0)),
                "income_rate_per_min": 0.0, "power": {"provided": power_provided, "drained": power_drained,
                "deficit": max(0, power_drained - power_provided)}, "harvester_count": int(economy.get("harvester_count", 0)),
            },
            "army": {"by_type": own_by_type, "total_value": int(data.get("military", {}).get("army_value", 0)), "tech_level": self._tech_level(buildings)},
            "enemy": {"clusters": [{k: v for k, v in c.items() if k != "center"} for c in clusters],
                      "buildings": [{"semantic_id": b["semantic_id"], "actor_type": b["actor_type"], "zone": b.get("zone"),
                                     "confidence": b["confidence"], "targetable": b["targetable"]} for b in enemy_buildings]},
            "map": {"name": data.get("map_info", {}).get("map_name", "agenda.oramap"),
                    "width": int(data.get("map_info", {}).get("width", 130)), "height": int(data.get("map_info", {}).get("height", 74)),
                    "explored_pct": float(data.get("explored_percent", 0)), "contested_zones": self._contested(units, enemies),
                    "zones": AGENDA_ZONES, "poi": self._poi_view(buildings, enemy_buildings)},
            "units": units, "buildings": buildings,
        }
        for event in low_level_events:
            self._append_engine_alert(event, tick)
        self._derive_alerts(battlefield, tick)
        self._previous_units = units
        self._previous_enemies = enemies
        self._last_battlefield = battlefield
        return battlefield

    @staticmethod
    def _tech_level(buildings: Iterable[Mapping[str, Any]]) -> int:
        names = {str(b.get("actor_type", "")).lower() for b in buildings}
        if names & {"atek", "stek"}:
            return 3
        if names & {"weap", "fix", "dome"}:
            return 2
        if names & {"barr", "tent", "proc"}:
            return 1
        return 0

    @staticmethod
    def _contested(own: Iterable[Mapping[str, Any]], enemy: Iterable[Mapping[str, Any]]) -> list[str]:
        own_zones = {x.get("zone") for x in own if x.get("can_attack") and x.get("zone")}
        enemy_zones = {x.get("zone") for x in enemy if x.get("can_attack") and x.get("zone")}
        return sorted(own_zones & enemy_zones)

    @staticmethod
    def _poi_view(buildings: Iterable[Mapping[str, Any]], enemy_buildings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        all_buildings = list(buildings) + list(enemy_buildings)
        result = []
        for poi_id, poi in AGENDA_POIS.items():
            occupants = [b for b in all_buildings if b.get("actor_type") == poi["actor_type"] and
                          math.hypot(float(b.get("cell_x", 0)) - poi["x"], float(b.get("cell_y", 0)) - poi["y"]) <= 2]
            result.append({"semantic_id": poi_id, "kind": poi["kind"], "cell": {"x": poi["x"], "y": poi["y"]},
                           "owner": occupants[0].get("owner_side") if occupants else None,
                           "targetable": bool(occupants or poi["kind"] in {"oil", "hospital"})})
        return result

    def _append_engine_alert(self, event: Mapping[str, Any], tick: int) -> None:
        event_type = str(event.get("type", ""))
        if event_type not in {"under_attack", "unit_destroyed", "own_building_destroyed", "production_complete", "enemy_spotted", "building_discovered"}:
            return
        if event_type in {"unit_destroyed", "own_building_destroyed"} and event.get("actor_id") is not None:
            self.registry.mark_destroyed(int(event["actor_id"]))
        severity = "critical" if event_type in {"under_attack", "own_building_destroyed"} else "info"
        self.alerts.append({"type": event_type, "source": "engine", "severity": severity,
                            "tick": int(event.get("tick", tick)), "message": event_type.replace("_", " "),
                            "zone": event.get("zone_hint"),
                            # Never forward actor ids (including ids embedded
                            # in engine event_id strings) through the Agent
                            # contract. Resolution happens in the registry.
                            "details": {} },
                           dedupe_key=f"{event_type}:{event.get('actor_id')}:{event.get('target_actor_id')}", tick=tick)

    def _derive_alerts(self, field: Mapping[str, Any], tick: int) -> None:
        economy = field["economy"]
        if economy["cash"] < 500 and self._last_battlefield is not None:
            self.alerts.append({"type": "low_cash", "source": "semantic", "severity": "warning", "tick": tick,
                                "message": "cash below 500 while production may be pending"}, dedupe_key="low_cash", tick=tick)
        deficit = economy["power"]["deficit"]
        if deficit > 0:
            self.alerts.append({"type": "low_power", "source": "semantic", "severity": "warning", "tick": tick,
                                "message": "power deficit detected"}, dedupe_key="low_power", tick=tick)
        for cluster in field["enemy"]["clusters"]:
            own_power = sum(1 for u in field["units"] if u.get("zone") == cluster["zone"] and u.get("can_attack"))
            if cluster["power"] > max(1, own_power * 2):
                self.alerts.append({"type": "zone_threat", "source": "semantic", "severity": "critical", "tick": tick,
                                    "zone": cluster["zone"], "message": f"enemy power threatens {cluster['zone']}"},
                                   dedupe_key=f"zone_threat:{cluster['zone']}", tick=tick)
        for poi in field["map"]["poi"]:
            if poi["owner"] is None:
                self.alerts.append({"type": "opportunity", "source": "semantic", "severity": "info", "tick": tick,
                                    "message": f"capturable {poi['semantic_id']}", "details": {"poi": poi["semantic_id"]}},
                                   dedupe_key=f"opportunity:{poi['semantic_id']}", tick=tick)

    def battlefield(self, filter_name: str | None = None) -> dict[str, Any]:
        if self._last_battlefield is None:
            raise RuntimeError("observation_disconnected")
        if self.cache.frame is not None and self.cache.frame.disconnected:
            raise RuntimeError("observation_disconnected")
        if filter_name is None or filter_name == "all":
            return self._last_battlefield
        valid = {"economy", "army", "buildings", "enemies", "map"}
        if filter_name in valid:
            value = self._last_battlefield["enemy"] if filter_name == "enemies" else self._last_battlefield[filter_name]
            return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id, "tick": self._last_battlefield["tick"],
                    "stale": self._last_battlefield["stale"], filter_name: value}
        if filter_name.startswith("zone:") and filter_name[5:] in AGENDA_ZONES:
            zone = filter_name[5:]
            return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id, "tick": self._last_battlefield["tick"],
                    "stale": self._last_battlefield["stale"], "zone": zone,
                    "units": [u for u in self._last_battlefield["units"] if u.get("zone") == zone],
                    "enemies": [e for e in self._last_battlefield["enemy"]["clusters"] if e.get("zone") == zone]}
        raise ValueError("invalid_filter")


def _public_missions(status: Mapping[str, Any], *, include_terminal: bool = False) -> dict[str, Any]:
    terminal = {"succeeded", "failed", "cancelled"}
    missions = []
    for mission in status.get("missions", []):
        if not include_terminal and mission.get("status") in terminal:
            continue
        item = dict(mission)
        assigned = []
        for unit in item.get("assigned_units", []):
            # C# status can contain actor_id for its own audit, but the MCP
            # boundary must never return it to an Agent.
            assigned.append({k: v for k, v in unit.items() if k != "actor_id"})
        item["assigned_units"] = assigned
        missions.append(item)
    result = {k: v for k, v in status.items() if k not in {"missions", "internal_actor_ids"}}
    result["missions"] = missions
    return result


class Phase1cFacade:
    """Five formal operations used by a semantic MCP server."""

    def __init__(self, session_id: str, *, runtime_root: str | os.PathLike[str] | None = None,
                 aggregator: SemanticAggregator | None = None, mission_client: MissionClient | None = None,
                 bridge_client: Any | None = None):
        self.session_id = session_id
        self.aggregator = aggregator or SemanticAggregator(session_id)
        root = runtime_root or os.environ.get("RL_MISSION_DIR") or os.environ.get("RL_OBSERVATION_DIR") or "."
        self.missions = mission_client or MissionClient(session_id, root)
        # Offline contract/tests may have grpcio installed but no live game.
        # Only auto-create a network client when the MCP/live environment has
        # explicitly selected a session; callers can still inject a client.
        if bridge_client is None and os.environ.get("RL_SESSION_ID"):
            try:
                from openra_env.server.bridge_client import BridgeClient
                bridge_client = BridgeClient(session_id=session_id, observation_dir=os.environ.get("RL_OBSERVATION_DIR"))
            except Exception:
                bridge_client = None
        self.bridge_client = bridge_client
        event_root = os.environ.get("RL_EVENT_DIR") or os.environ.get("RL_OBSERVATION_DIR") or root
        self.event_path = Path(event_root) / session_id / "low-level-events.jsonl"
        self._event_count = 0

    def _refresh(self) -> None:
        """Pull one gRPC frame (or A1.5 snapshot) before a read operation."""
        if self.bridge_client is None:
            return
        result = self.bridge_client.read_observation(self.session_id)
        raw = raw_observation_to_dict(result.observation)
        events = load_low_level_events(self.event_path, session_id=self.session_id)
        new_events = events[self._event_count:]
        self._event_count = len(events)
        self.aggregator.ingest(raw, source=result.source, low_level_events=new_events)

    def read_battlefield(self, filter: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return _error("INVALID_SCHEMA", f"unknown arguments: {sorted(kwargs)}")
        try:
            self._refresh()
            return _envelope(self.aggregator.battlefield(filter))
        except ValueError:
            return _error("INVALID_FILTER", "filter must be all, economy, army, buildings, enemies, map or zone:<id>")
        except RuntimeError as exc:
            return _error("OBSERVATION_DISCONNECTED" if "disconnect" in str(exc) else "OBSERVATION_STALE", str(exc), True)
        except Exception as exc:
            return _error("OBSERVATION_DISCONNECTED", str(exc), True)

    def get_alerts(self, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return _error("INVALID_SCHEMA", f"unknown arguments: {sorted(kwargs)}")
        try:
            self._refresh()
        except Exception as exc:
            if self.aggregator.cache.frame is None:
                return _error("OBSERVATION_DISCONNECTED", str(exc), True)
        frame = self.aggregator.cache.frame
        if frame is not None:
            if frame.disconnected:
                return _error("OBSERVATION_DISCONNECTED", "no observation received for more than 5 seconds", True)
            if frame.stale:
                self.aggregator.alerts.append({"type": "disconnected", "source": "semantic", "severity": "warning",
                                               "tick": int(frame.raw.get("tick", 0)),
                                               "message": "observation is older than 2 seconds",
                                               "details": {"stale": True}}, dedupe_key="observation_stale",
                                              tick=int(frame.raw.get("tick", 0)))
        return _envelope(self.aggregator.alerts.read(cursor))

    def read_missions(self, include_terminal: bool = False, mission_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return _error("INVALID_SCHEMA", f"unknown arguments: {sorted(kwargs)}")
        try:
            return _envelope(_public_missions(self.missions.read_missions(mission_id), include_terminal=include_terminal))
        except MissionProtocolError as exc:
            return _error(exc.code, exc.message, exc.code in {"STATUS_UNAVAILABLE"})

    def issue_mission(self, mission: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return _error("INVALID_SCHEMA", f"unknown arguments: {sorted(kwargs)}")
        try:
            observed_tick = self.aggregator.cache.frame.raw.get("tick", 0) if self.aggregator.cache.frame else 0
            event = self.missions.issue_mission(dict(mission), observed_tick=int(observed_tick))
            return _envelope({"mission_id": event["mission"]["id"], "revision": event["revision"], "status": "pending", "session_id": self.session_id})
        except MissionProtocolError as exc:
            return _error(exc.code, exc.message, exc.code in {"COMMAND_WRITE_FAILED", "STATUS_UNAVAILABLE"})
        except (OSError, ValueError) as exc:
            return _error("COMMAND_WRITE_FAILED", str(exc), True)

    def cancel_mission(self, mission_id: str, reason: str = "operator_cancel", **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return _error("INVALID_SCHEMA", f"unknown arguments: {sorted(kwargs)}")
        try:
            event = self.missions.cancel_mission(mission_id, reason)
            return _envelope({"mission_id": mission_id, "revision": event["revision"], "status": "cancel_pending", "session_id": self.session_id})
        except MissionProtocolError as exc:
            return _error(exc.code, exc.message, exc.code in {"COMMAND_WRITE_FAILED", "STATUS_UNAVAILABLE"})


def raw_observation_to_dict(observation: Any) -> dict[str, Any]:
    """Convert a protobuf object without exposing it at the MCP boundary."""
    from openra_env.server.bridge_client import observation_to_dict

    data = observation_to_dict(observation)
    data.update({"episode_id": observation.episode_id, "observation_sequence": observation.observation_sequence,
                 "observed_at_unix_ms": observation.observed_at_unix_ms, "serialization_ms": observation.serialization_ms,
                 "schema_version": observation.schema_version})
    return data


def load_low_level_events(path: str | os.PathLike[str], *, session_id: str) -> list[dict[str, Any]]:
    """Read a session sidecar event log, tolerating a torn final JSONL line."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    events: list[dict[str, Any]] = []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
        if event.get("session_id") == session_id:
            events.append(event)
    return events
