"""Phase 1d read-only demo bundle and local dashboard server.

The demo consumes committed Phase 1c evidence. It never issues commands and
never mutates engine state; an optional noVNC URL is embedded for a live or
replay viewer, while the fallback view remains useful without Docker.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = ROOT.parent / "document" / "evidence" / "phase1c"
DEFAULT_DEMO = ROOT / "commander" / "demo"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _step(evidence: dict[str, Any], name: str) -> dict[str, Any]:
    for row in evidence.get("transcript", []):
        if row.get("step") == name:
            return row.get("result") or {}
    return {}


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _write_missions_yaml(path: Path, session_id: str, missions: list[dict[str, Any]], generated_tick: int) -> None:
    lines = ["schema_version: 1", f"session_id: {_yaml_scalar(session_id)}", f"generated_at_tick: {generated_tick}", "missions:"]
    for mission in missions:
        lines.append(f"  - id: {_yaml_scalar(mission.get('id'))}")
        for key in ("command_revision", "type", "priority", "status", "ingested_at_tick", "accepted_at_tick",
                    "started_at_tick", "completed_at_tick", "failure_reason", "blocker", "progress"):
            if key in mission and mission[key] is not None:
                lines.append(f"    {key}: {_yaml_scalar(mission[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _timeline(audit: list[dict[str, Any]], replay: dict[str, Any]) -> list[dict[str, Any]]:
    frames = int(replay.get("max_frame") or 0)
    rows = []
    for event in audit:
        tick = int(event.get("tick", 0))
        rows.append({**event, "replay_frame": tick // 3, "within_replay": tick // 3 <= frames})
    return rows


def build_state(evidence_root: Path = DEFAULT_EVIDENCE, novnc_url: str = "") -> dict[str, Any]:
    evidence = _read_json(evidence_root / "phase1c-live-mcp.json")
    regression = _read_json(evidence_root / "phase1c-live-mcp-regression.json")
    battlefield = _step(evidence, "read_battlefield").get("data", {})
    alerts = _step(evidence, "get_alerts").get("data", {}).get("alerts", [])
    final = _step(evidence, "read_missions_final").get("data", {})
    replay = dict(evidence.get("replay", {}))
    replay["alignment_note"] = "world tick → network frame: tick // 3; every audit marker is inside the recorded replay timeline"
    timeline = _timeline(evidence.get("audit", []), replay)
    checks = dict(evidence.get("checks", {}))
    checks["replay_audit_timeline"] = all(row.get("within_replay") for row in timeline) if timeline else False
    checks["five_seed_regression"] = bool(regression.get("pass"))
    return {
        "schema_version": 1,
        "session_id": evidence.get("session_id"),
        "canonical": evidence.get("canonical", {}),
        "battlefield": battlefield,
        "alerts": alerts,
        "alert_cursor": _step(evidence, "get_alerts").get("data", {}).get("cursor"),
        "missions": final.get("missions", []),
        "mission_status": final,
        "audit": evidence.get("audit", []),
        "timeline": timeline,
        "replay": replay,
        "viewer": {"novnc_url": novnc_url or None, "mode": "novnc" if novnc_url else "replay-fallback"},
        "checks": checks,
        "regression": regression.get("checks", {}),
        "sources": ["phase1c-live-mcp.json", "phase1c-live-mcp-regression.json", "phase1c-live-mcp.orarep"],
    }


def build_bundle(evidence_root: Path = DEFAULT_EVIDENCE, output_dir: Path | None = None, novnc_url: str = "") -> dict[str, Any]:
    output = output_dir or (ROOT.parent / "document" / "evidence" / "phase1d")
    output.mkdir(parents=True, exist_ok=True)
    state = build_state(evidence_root, novnc_url)
    (output / "phase1d-demo-state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_missions_yaml(output / "missions.yaml", state["session_id"], state["missions"], state["mission_status"].get("generated_at_tick", 0))
    (output / "phase1d-replay-audit-timeline.json").write_text(json.dumps({"schema_version": 1, "session_id": state["session_id"], "timeline": state["timeline"], "replay": state["replay"]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "profile": "phase1d-demo",
        "canonical": state["canonical"],
        "session_id": state["session_id"],
        "viewer_mode": state["viewer"]["mode"],
        "required_panels": ["battlefield", "missions", "alerts", "replay_audit_timeline", "gate_checks"],
        "artifacts": ["phase1d-demo-state.json", "missions.yaml", "phase1d-replay-audit-timeline.json"],
        "checks": state["checks"],
    }
    (output / "phase1d-demo-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return state


class _Handler(BaseHTTPRequestHandler):
    state: dict[str, Any] = {}
    demo_dir: Path = DEFAULT_DEMO
    evidence_root: Path = DEFAULT_EVIDENCE
    novnc_url: str = ""

    def _send(self, body: bytes, content_type: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/api/state":
            self.state = build_state(self.evidence_root, self.novnc_url)
            self._send(json.dumps(self.state, ensure_ascii=False).encode("utf-8"))
            return
        if route in {"/", "/index.html"}:
            self._send((self.demo_dir / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        self._send(b"not found", "text/plain; charset=utf-8", 404)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(evidence_root: Path, demo_dir: Path, port: int, novnc_url: str) -> None:
    handler = type("Phase1dHandler", (_Handler,), {"evidence_root": evidence_root, "demo_dir": demo_dir, "novnc_url": novnc_url})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"phase1d demo ready: http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or serve the Phase 1d read-only demo")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE)
    common.add_argument("--output-dir", type=Path, default=ROOT.parent / "document" / "evidence" / "phase1d")
    common.add_argument("--novnc-url", default="")
    sub.add_parser("bundle", parents=[common])
    server_parser = sub.add_parser("serve", parents=[common])
    server_parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    if args.command == "bundle":
        state = build_bundle(args.evidence_root, args.output_dir, args.novnc_url)
        print(json.dumps({"pass": all(state["checks"].values()), "output_dir": str(args.output_dir), "session_id": state["session_id"]}, ensure_ascii=False))
        return 0 if all(state["checks"].values()) else 2
    build_bundle(args.evidence_root, args.output_dir, args.novnc_url)
    serve(args.evidence_root, ROOT / "commander" / "demo", args.port, args.novnc_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
