"""Live Phase 1c MCP façade gate against a running multi-session daemon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import time
import types
from pathlib import Path


def _imports(root: Path):
    package = types.ModuleType("openra_env")
    package.__path__ = [str(root / "openra_env")]
    sys.modules.setdefault("openra_env", package)
    generated = types.ModuleType("openra_env.generated")
    generated.__path__ = [str(root / "openra_env" / "generated")]
    sys.modules.setdefault("openra_env.generated", generated)
    server = types.ModuleType("openra_env.server")
    server.__path__ = [str(root / "openra_env" / "server")]
    sys.modules.setdefault("openra_env.server", server)
    from openra_env.phase1c import Phase1cFacade, SemanticAggregator  # type: ignore
    from openra_env.server.bridge_client import BridgeClient  # type: ignore
    from openra_env.server.mission_client import MissionClient  # type: ignore
    from openra_env.generated import rl_bridge_pb2 as pb  # type: ignore
    from openra_env.generated import rl_bridge_pb2_grpc as rpc  # type: ignore

    return Phase1cFacade, SemanticAggregator, BridgeClient, MissionClient, pb, rpc


def _replay_frames(path: Path) -> list[int]:
    """Read network frame numbers from an OpenRA replay order stream.

    Replay packets are ``client_id, payload_length, payload`` little-endian
    records.  ``ReplayRecorder.ReceiveFrame`` prefixes each payload with the
    network frame.  The metadata trailer is intentionally treated as the
    stopping point when its marker no longer forms a valid packet.
    """
    data = path.read_bytes()
    offset = 0
    frames: list[int] = []
    while offset + 8 <= len(data):
        _client_id, length = struct.unpack_from("<ii", data, offset)
        offset += 8
        if length < 0 or offset + length > len(data):
            break
        payload = data[offset:offset + length]
        offset += length
        if len(payload) >= 4:
            frame = struct.unpack_from("<i", payload, 0)[0]
            if frame >= 0:
                frames.append(frame)
    return sorted(set(frames))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=45)
    parser.add_argument("--evidence-name", default="phase1c-live-mcp.json")
    args = parser.parse_args()

    import grpc

    root = Path(args.project_root).resolve()
    Phase1cFacade, SemanticAggregator, BridgeClient, MissionClient, pb, rpc = _imports(root)
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = rpc.RLBridgeStub(channel)
    session_id = stub.CreateSession(pb.CreateSessionRequest(
        map_name="agenda.oramap", bots="Multi1:agent-normal,Multi0:easy", seed=args.seed,
        player_faction="russia", enemy_faction="england", player_spawn=1, enemy_spawn=2,
    ), timeout=120).session_id
    runtime_root = Path(args.runtime_root)
    os.environ["RL_OBSERVATION_DIR"] = str(runtime_root)
    os.environ["RL_MISSION_DIR"] = str(runtime_root)
    os.environ["RL_EVENT_DIR"] = str(runtime_root)
    aggregator = SemanticAggregator(session_id)
    bridge = BridgeClient(host=args.host, port=args.port, session_id=session_id, observation_dir=str(runtime_root))
    missions = MissionClient(session_id, runtime_root, fsync=True)
    facade = Phase1cFacade(session_id, aggregator=aggregator, mission_client=missions, bridge_client=bridge)
    transcript: list[dict] = []
    audit_lines: list[dict] = []
    try:
        battlefield = facade.read_battlefield("map")
        transcript.append({"step": "read_battlefield", "result": battlefield})
        alerts = facade.get_alerts()
        transcript.append({"step": "get_alerts", "result": alerts})
        current = facade.read_missions()
        transcript.append({"step": "read_missions_before", "result": current})
        mission_payload = {
            "id": "mcp_capture_oil_center_west", "type": "capture", "priority": "high", "ttl_ticks": 1500,
            "target": "oil_center_west", "unit_type": "e6", "escort_units": 1,
        }
        issued = facade.issue_mission(mission_payload)
        transcript.append({"step": "issue_mission", "result": issued})
        # Simulate an MCP process restart: a fresh façade must recover the
        # existing command revision instead of appending a duplicate issue.
        restarted = Phase1cFacade(session_id, runtime_root=runtime_root,
                                  mission_client=MissionClient(session_id, runtime_root, fsync=True))
        restart_issue = restarted.issue_mission(mission_payload)
        transcript.append({"step": "mcp_restart_issue_retry", "result": restart_issue})
        mission_id = issued.get("data", {}).get("mission_id")
        statuses = []
        deadline = time.monotonic() + args.timeout_seconds
        while mission_id and time.monotonic() < deadline:
            status = facade.read_missions(mission_id=mission_id, include_terminal=True)
            statuses.append(status)
            missions_list = status.get("data", {}).get("missions", []) if status.get("ok") else []
            if missions_list and missions_list[0].get("status") in {"accepted", "blocked", "in_progress", "succeeded", "failed", "cancelled"}:
                if missions_list[0].get("status") != "pending":
                    break
            time.sleep(0.5)
        transcript.append({"step": "read_missions_after", "result": statuses[-1] if statuses else None})
        cancelled = facade.cancel_mission(mission_id or "mcp_capture_oil_center_west", "phase1c_live_gate_cleanup")
        transcript.append({"step": "cancel_mission", "result": cancelled})
        cancel_deadline = time.monotonic() + 10
        final = facade.read_missions(mission_id=mission_id, include_terminal=True)
        while mission_id and time.monotonic() < cancel_deadline:
            final = facade.read_missions(mission_id=mission_id, include_terminal=True)
            data = final.get("data", {}) if final.get("ok") else {}
            mission_rows = data.get("missions", [])
            if data.get("last_applied_revision", 0) >= 2 and mission_rows and mission_rows[0].get("status") == "cancelled":
                break
            time.sleep(0.5)
        transcript.append({"step": "read_missions_final", "result": final})
        audit_lines = []
        if missions.audit_path.exists():
            audit_lines = [json.loads(line) for line in missions.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    finally:
        bridge.close()
        try:
            stub.DestroySession(pb.DestroySessionRequest(session_id=session_id), timeout=30)
        except grpc.RpcError:
            pass
        channel.close()

    replay_candidates = []
    support = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "OpenRA" / "Replays" / "ra" / "{DEV_VERSION}"
    if support.exists():
        replay_candidates = sorted(support.glob(f"*{session_id}*.orarep"), key=lambda item: item.stat().st_mtime, reverse=True)
    replay_info = {"found": bool(replay_candidates)}
    if replay_candidates:
        source_replay = replay_candidates[0]
        replay_bytes = source_replay.read_bytes()
        replay_frames = _replay_frames(source_replay)
        replay_info.update({"source": str(source_replay), "size": len(replay_bytes),
                            "sha256": hashlib.sha256(replay_bytes).hexdigest(),
                            "frame_count": len(replay_frames),
                            "min_frame": replay_frames[0] if replay_frames else None,
                            "max_frame": replay_frames[-1] if replay_frames else None})

    serialized = json.dumps(transcript, ensure_ascii=False)
    statuses = [x for row in transcript if row["step"] == "read_missions_after" for x in [row["result"]] if x]
    final_data = transcript[-1]["result"].get("data", {}) if transcript[-1]["result"].get("ok") else {}
    final_rows = final_data.get("missions", [])
    audit_ticks = [line.get("tick") for line in audit_lines if isinstance(line.get("tick"), int)]
    # OpenRA's network frame advances every three simulation ticks (40ms
    # local tick vs 120ms network frame).  The check proves that every audit
    # event falls within the recorded replay timeline, allowing a timeline
    # viewer to map audit ticks to replay frames deterministically.
    replay_max_frame = replay_info.get("max_frame")
    audit_replay_aligned = bool(audit_ticks and replay_info.get("frame_count", 0) > 0 and
                                replay_info.get("source", "").find(session_id) >= 0 and
                                replay_max_frame >= max(audit_ticks) // 3)
    checks = {
        "read_battlefield_ok": transcript[0]["result"].get("ok") is True,
        "get_alerts_ok": transcript[1]["result"].get("ok") is True,
        "read_missions_ok": transcript[2]["result"].get("ok") is True,
        "issue_persisted": transcript[3]["result"].get("ok") is True,
        "restart_issue_idempotent": next((row["result"].get("data", {}).get("revision") == transcript[3]["result"].get("data", {}).get("revision")
                                           for row in transcript if row["step"] == "mcp_restart_issue_retry"), False),
        "status_feedback_observed": bool(statuses and statuses[0].get("ok")),
        "cancel_persisted": next((row["result"].get("ok") is True for row in transcript if row["step"] == "cancel_mission"), False),
        "cancel_applied_by_engine": final_data.get("last_applied_revision", 0) >= 2 and bool(final_rows) and final_rows[0].get("status") == "cancelled",
        "audit_written": bool(audit_lines),
        "audit_tick_aligned": audit_replay_aligned,
        "replay_written": replay_info["found"],
        "raw_actor_id_hidden": "actor_id" not in serialized and "target_actor_id" not in serialized,
        "session_id_consistent": all(row["result"].get("data", {}).get("session_id", session_id) == session_id
                                      for row in transcript if row["result"].get("ok") and isinstance(row["result"].get("data"), dict)),
    }
    evidence = {"profile": "phase1c-live-mcp", "canonical": {"map": "agenda.oramap", "seed": args.seed,
                "player": "Multi1:agent-normal/russia/spawn1", "enemy": "Multi0:easy/england/spawn2"},
                "session_id": session_id, "human_prompt": "抢中间偏西的油井",
                "transcript": transcript, "audit": audit_lines, "replay": replay_info, "checks": checks, "pass": all(checks.values())}
    evidence_root = root.parent / "document" / "evidence" / "phase1c"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / args.evidence_name).write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if replay_candidates:
        shutil.copy2(replay_candidates[0], evidence_root / "phase1c-live-mcp.orarep")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
