"""Verify the Phase 1 empty Steering runtime heartbeat.

The daemon must already be running in multi-session mode.  The script creates
the canonical session, waits for a heartbeat emitted by ``SteeringModule``
and requires ``steering_order_count=0``.  It does not issue actions or claim
that the natural full-game replay Gate passed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import types
from pathlib import Path


HEARTBEAT = re.compile(
    r"event=steering_heartbeat player=Multi1 tick=(?P<tick>\d+) steering_order_count=(?P<count>\d+)"
)


def load_proto(project_root: Path):
    package = types.ModuleType("openra_env")
    package.__path__ = [str(project_root / "openra_env")]
    sys.modules.setdefault("openra_env", package)
    generated = types.ModuleType("openra_env.generated")
    generated.__path__ = [str(project_root / "openra_env" / "generated")]
    sys.modules.setdefault("openra_env.generated", generated)
    from openra_env.generated import rl_bridge_pb2 as pb  # type: ignore
    from openra_env.generated import rl_bridge_pb2_grpc as rpc  # type: ignore

    return pb, rpc


def log_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--evidence-name", default="phase1a-steering-gate.json")
    args = parser.parse_args()

    import grpc

    project_root = Path(args.project_root).resolve()
    log_path = Path(args.log_path).resolve()
    initial_log = log_text(log_path)
    pb, rpc = load_proto(project_root)
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = rpc.RLBridgeStub(channel)
    request = pb.CreateSessionRequest(
        map_name="agenda.oramap",
        bots="Multi1:agent-normal,Multi0:easy",
        seed=42,
        player_faction="russia",
        enemy_faction="england",
        player_spawn=1,
        enemy_spawn=2,
    )
    session_id = ""
    for _ in range(30):
        try:
            session_id = stub.CreateSession(request, timeout=30).session_id
            if session_id:
                break
        except grpc.RpcError:
            time.sleep(1)
    if not session_id:
        raise RuntimeError("CreateSession did not return a session_id")
    started = time.monotonic()
    final_phase = "none"
    final_tick = 0
    heartbeat = None
    try:
        while time.monotonic() - started < args.timeout_seconds:
            state = stub.GetState(pb.StateRequest(session_id=session_id), timeout=10)
            final_phase = state.phase
            final_tick = state.tick
            appended = log_text(log_path)
            if initial_log and appended.startswith(initial_log):
                appended = appended[len(initial_log) :]
            match = HEARTBEAT.search(appended)
            if match:
                heartbeat = {
                    "tick": int(match.group("tick")),
                    "steering_order_count": int(match.group("count")),
                }
                break
            if final_phase == "error":
                break
            time.sleep(0.5)
    finally:
        try:
            stub.DestroySession(pb.DestroySessionRequest(session_id=session_id), timeout=30)
        except grpc.RpcError:
            pass
        channel.close()

    evidence = {
        "profile": "phase1a-steering-runtime",
        "session_id": session_id,
        "canonical": {
            "map": "agenda.oramap",
            "seed": 42,
            "player": "Multi1:agent-normal/russia/spawn1",
            "enemy": "Multi0:easy/england/spawn2",
        },
        "final_phase": final_phase,
        "final_tick": final_tick,
        "heartbeat": heartbeat,
        "pass": heartbeat is not None and heartbeat["steering_order_count"] == 0,
        "natural_full_replay": "not evaluated by this bounded runtime Gate",
    }
    evidence_root = project_root.parent / "document" / "evidence" / "phase1a"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / args.evidence_name).write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
