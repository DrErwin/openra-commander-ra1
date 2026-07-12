"""Compare canonical normal and agent-normal headless tick behavior.

The daemon must be started with ``RL_ALLOW_OBSERVATIONLESS_SESSIONS=true``.
The flag is intentionally opt-in and is only used by this baseline harness;
the canonical agent route remains ``agent-normal`` plus ObservationTrait.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import types
from pathlib import Path


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


def wait_playing(stub, pb, session_id: str):
    for _ in range(120):
        state = stub.GetState(pb.StateRequest(session_id=session_id), timeout=5)
        if state.phase == "playing":
            return state
        if state.phase == "error":
            raise RuntimeError(f"{state.error_code}: {state.error_message}")
        time.sleep(0.25)
    raise TimeoutError(f"session {session_id} did not reach playing")


def run_profile(stub, pb, bots: str, duration_s: float, seed: int):
    session_id = stub.CreateSession(
        pb.CreateSessionRequest(
            map_name="agenda.oramap",
            bots=bots,
            seed=seed,
            player_faction="russia",
            enemy_faction="england",
            player_spawn=1,
            enemy_spawn=2,
        ),
        timeout=30,
    ).session_id
    try:
        initial = wait_playing(stub, pb, session_id)
        assert initial.player_faction == "russia"
        assert initial.enemy_faction == "england"
        assert initial.player_spawn == 1
        assert initial.enemy_spawn == 2

        first_observation = None
        if bots.startswith("Multi1:agent-normal"):
            stream = stub.StreamObservations(pb.StateRequest(session_id=session_id), timeout=30)
            first_observation = next(stream)
            stream.cancel()

        started = time.monotonic()
        start_tick = stub.GetState(pb.StateRequest(session_id=session_id), timeout=5).tick
        time.sleep(duration_s)
        final = stub.GetState(pb.StateRequest(session_id=session_id), timeout=5)
        elapsed = max(0.001, time.monotonic() - started)
        result = {
            "bots": bots,
            "session_id": session_id,
            "start_tick": start_tick,
            "final_tick": final.tick,
            "tick_delta": final.tick - start_tick,
            "elapsed_seconds": round(elapsed, 3),
            "tick_rate": round((final.tick - start_tick) / elapsed, 3),
            "phase": final.phase,
            "player_faction": final.player_faction,
            "enemy_faction": final.enemy_faction,
            "player_spawn": final.player_spawn,
            "enemy_spawn": final.enemy_spawn,
        }
        if first_observation is not None:
            result["first_observation"] = {
                "tick": first_observation.tick,
                "sequence": first_observation.observation_sequence,
                "schema_version": first_observation.schema_version,
            }
        return result
    finally:
        stub.DestroySession(pb.DestroySessionRequest(session_id=session_id), timeout=15)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--duration-seconds", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import grpc

    project_root = Path(args.project_root).resolve()
    pb, rpc = load_proto(project_root)
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = rpc.RLBridgeStub(channel)
    try:
        baseline = run_profile(stub, pb, "Multi1:normal,Multi0:easy", args.duration_seconds, args.seed)
        agent = run_profile(stub, pb, "Multi1:agent-normal,Multi0:easy", args.duration_seconds, args.seed)
    finally:
        channel.close()

    baseline_rate = baseline["tick_rate"]
    agent_rate = agent["tick_rate"]
    regression_pct = 0.0 if baseline_rate == 0 else max(0.0, (baseline_rate - agent_rate) / baseline_rate * 100)
    evidence = {
        "profile": "canonical-baseline-comparison",
        "seed": args.seed,
        "duration_seconds": args.duration_seconds,
        "baseline": baseline,
        "agent_normal": agent,
        "tick_rate_regression_percent": round(regression_pct, 3),
        "tick_regression_gate_percent": 5.0,
        "pass": regression_pct <= 5.0,
        "observationless_mode": "RL_ALLOW_OBSERVATIONLESS_SESSIONS=true",
    }
    assert evidence["pass"], evidence
    evidence_root = project_root.parent / "document" / "evidence" / "phase1a"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "phase1a-baseline.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
