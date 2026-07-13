"""Run one canonical autonomous session for replay evidence.

Set ``RL_RECORD_REPLAYS=true`` on the daemon to enable the multi-session
per-session recorder. This script does not inject actions or use the test
game-over hook by default: it waits for the normal ModularBot/easy game to
finish, or reports a timeout without claiming that the natural replay gate
passed. The test hook is used only by the separate bounded writer fixture.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--fast-forward-batch",
        type=int,
        default=0,
        help="advance this many autonomous ticks per RPC; 0 uses wall-clock polling",
    )
    args = parser.parse_args()

    import grpc

    project_root = Path(args.project_root).resolve()
    pb, rpc = load_proto(project_root)
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = rpc.RLBridgeStub(channel)
    request = pb.CreateSessionRequest(
        map_name="agenda.oramap",
        bots="Multi1:agent-normal,Multi0:easy",
        seed=args.seed,
        player_faction="russia",
        enemy_faction="england",
        player_spawn=1,
        enemy_spawn=2,
    )
    session_id = ""
    for _ in range(30):
        response = stub.CreateSession(request, timeout=300)
        session_id = response.session_id
        if session_id:
            break
        time.sleep(1)
    if not session_id:
        raise RuntimeError("CreateSession returned an empty session_id")

    started = time.monotonic()
    samples = []
    terminal = {"game_over", "error"}
    final = None
    natural_terminal = False
    observer_stop = threading.Event()
    observer_count = 0
    observation_done = False

    def consume_observations():
        nonlocal observer_count, observation_done
        try:
            for observation in stub.StreamObservations(
                pb.StateRequest(session_id=session_id), timeout=args.timeout_seconds + 30
            ):
                observer_count += 1
                if observation.done:
                    observation_done = True
                    break
                if observer_stop.is_set():
                    break
        except Exception:
            # The state poll remains authoritative; stream closure is expected
            # when the world reaches game over or the test times out.
            return

    observer_thread = threading.Thread(target=consume_observations, name="replay-gate-observer", daemon=True)
    observer_thread.start()
    try:
        while time.monotonic() - started < args.timeout_seconds:
            state = stub.GetState(pb.StateRequest(session_id=session_id), timeout=10)
            final = state
            samples.append(
                {
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "phase": state.phase,
                    "tick": state.tick,
                }
            )
            if state.phase in terminal:
                natural_terminal = True
                break
            if observation_done:
                natural_terminal = True
                break
            if args.fast_forward_batch > 0 and state.phase == "playing":
                try:
                    observation = stub.FastAdvance(
                        pb.FastAdvanceRequest(session_id=session_id, ticks=args.fast_forward_batch),
                        timeout=120,
                    )
                except grpc.RpcError as error:
                    samples.append({"rpc_error": error.code().name, "details": error.details()})
                    break
                if observation.done:
                    natural_terminal = True
                    break
            else:
                time.sleep(5)
    finally:
        observer_stop.set()
        # Explicitly dispose even after game-over so the per-session replay
        # recorder flushes its metadata and closes the file before the daemon
        # is stopped by the harness.
        try:
            stub.DestroySession(pb.DestroySessionRequest(session_id=session_id), timeout=30)
        except grpc.RpcError:
            pass
        observer_thread.join(timeout=2)
        channel.close()

    evidence = {
        "profile": "canonical-natural-replay",
        "seed": args.seed,
        "session_id": session_id,
        "timeout_seconds": args.timeout_seconds,
        "final_phase": final.phase if final is not None else "none",
        "final_tick": final.tick if final is not None else 0,
        "natural_terminal": natural_terminal,
        "observation_done": observation_done,
        "fast_forward_batch": args.fast_forward_batch,
        "observation_count": observer_count,
        "samples": samples[-10:],
    }
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["natural_terminal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
