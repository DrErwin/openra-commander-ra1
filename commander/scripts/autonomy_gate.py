"""Bounded no-action smoke test for the Phase 1 ``agent-normal`` bot.

The daemon must already be running in multi-session mode.  This gate opens
only the read-only observation stream: it never calls ``FastAdvance`` and it
never sends an ``AgentAction``.  The bounded run proves that the normal
ModularBot continues to tick while the observation/Steering traits are
attached, and records observable autonomous activity (orders, construction,
or production) without requiring a full match to reach game over.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import types
from pathlib import Path


def load_proto(project_root: Path):
    """Load generated bindings without importing optional OpenEnv packages."""
    package = types.ModuleType("openra_env")
    package.__path__ = [str(project_root / "openra_env")]
    sys.modules.setdefault("openra_env", package)
    generated = types.ModuleType("openra_env.generated")
    generated.__path__ = [str(project_root / "openra_env" / "generated")]
    sys.modules.setdefault("openra_env.generated", generated)
    from openra_env.generated import rl_bridge_pb2 as pb  # type: ignore
    from openra_env.generated import rl_bridge_pb2_grpc as rpc  # type: ignore

    return pb, rpc


def snapshot(observation):
    """Return stable, compact metrics from one protobuf observation."""
    return {
        "tick": observation.tick,
        "sequence": observation.observation_sequence,
        "units": len(observation.units),
        "buildings": len(observation.buildings),
        "harvesters": observation.economy.harvester_count,
        "production_entries": len(observation.production),
        "army_value": observation.military.army_value,
        "order_count": observation.military.order_count,
        "cash": observation.economy.cash,
        "ore": observation.economy.ore,
    }


def state_snapshot(state):
    return {
        "phase": state.phase,
        "tick": state.tick,
        "player_faction": state.player_faction,
        "enemy_faction": state.enemy_faction,
        "player_spawn": state.player_spawn,
        "enemy_spawn": state.enemy_spawn,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--evidence-name", default="phase1a-autonomy-smoke.json")
    args = parser.parse_args()

    if args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")

    import grpc

    project_root = Path(args.project_root).resolve()
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
    session_id = stub.CreateSession(request, timeout=120).session_id
    if not session_id:
        raise RuntimeError("CreateSession returned an empty session_id")

    observations = []
    states = []
    resolved_configuration = None
    started = time.monotonic()
    final_phase = "none"
    final_tick = 0
    stream = None
    try:
        stream = stub.StreamObservations(
            pb.StateRequest(session_id=session_id),
            timeout=args.duration_seconds + 30,
        )
        while time.monotonic() - started < args.duration_seconds:
            try:
                observation = next(stream)
            except StopIteration:
                break
            observations.append(snapshot(observation))
            if observation.done:
                break
            state = stub.GetState(pb.StateRequest(session_id=session_id), timeout=10)
            states.append(state_snapshot(state))
            if state.phase in {"playing", "game_over"}:
                resolved_configuration = states[-1]
            final_phase = state.phase
            final_tick = state.tick
    finally:
        if stream is not None:
            stream.cancel()
        try:
            final = stub.GetState(pb.StateRequest(session_id=session_id), timeout=10)
            final_phase = final.phase
            final_tick = final.tick
            if final.phase in {"playing", "game_over"}:
                resolved_configuration = state_snapshot(final)
        except grpc.RpcError:
            pass
        try:
            stub.DestroySession(pb.DestroySessionRequest(session_id=session_id), timeout=30)
        except grpc.RpcError:
            pass
        channel.close()

    first = observations[0] if observations else None
    last = observations[-1] if observations else None
    ticks = [item["tick"] for item in observations]
    sequences = [item["sequence"] for item in observations]
    max_order_count = max((item["order_count"] for item in observations), default=0)
    max_buildings = max((item["buildings"] for item in observations), default=0)
    max_production_entries = max((item["production_entries"] for item in observations), default=0)

    # A normal ModularBot activity signal is required.  It is deliberately
    # broad because map timing can vary: either issued orders, construction,
    # or an active production queue proves that the bot is doing work.
    activity_signal = (
        max_order_count > 0 or max_buildings > 0 or max_production_entries > 0
    )
    configuration_valid = resolved_configuration is not None and all(
        [
            resolved_configuration["player_faction"] == "russia",
            resolved_configuration["enemy_faction"] == "england",
            resolved_configuration["player_spawn"] == 1,
            resolved_configuration["enemy_spawn"] == 2,
        ]
    )
    evidence = {
        "profile": "phase1a-autonomy-smoke",
        "canonical": {
            "map": "agenda.oramap",
            "seed": 42,
            "player": "Multi1:agent-normal/russia/spawn1",
            "enemy": "Multi0:easy/england/spawn2",
        },
        "session_id": session_id,
        "duration_seconds": args.duration_seconds,
        "agent_actions_sent": 0,
        "final_phase": final_phase,
        "final_tick": final_tick,
        "resolved_configuration": resolved_configuration,
        "observation_count": len(observations),
        "first": first,
        "last": last,
        "max_order_count": max_order_count,
        "max_buildings": max_buildings,
        "max_production_entries": max_production_entries,
        "monotonic_ticks": all(a < b for a, b in zip(ticks, ticks[1:])),
        "monotonic_sequences": all(a < b for a, b in zip(sequences, sequences[1:])),
        "activity_signal": activity_signal,
        "configuration_valid": configuration_valid,
    }
    evidence["pass"] = (
        final_phase in {"playing", "game_over"}
        and len(observations) >= 5
        and evidence["monotonic_ticks"]
        and evidence["monotonic_sequences"]
        and evidence["activity_signal"]
        and evidence["configuration_valid"]
        and evidence["agent_actions_sent"] == 0
    )

    evidence_root = project_root.parent / "document" / "evidence" / "phase1a"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / args.evidence_name).write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
