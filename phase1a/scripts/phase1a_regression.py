"""Phase 1a live regression and fallback evidence collector.

The OpenRA multi-session daemon must already be running. The script creates
canonical sessions, reads gRPC and A1.5 snapshots, checks isolation, then
destroys every session and writes JSON evidence.
"""

from __future__ import annotations

import argparse
import json
import os
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


def read_fallback(root: Path, session_id: str, pb, cursor: int | None = None, minimum_sequence: int = 0):
    session_dir = root / session_id
    envelope_path = session_dir / "latest-observation.json"
    protobuf_path = session_dir / "latest-observation.pb"
    deadline = time.time() + 2
    while True:
        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if time.time() >= deadline:
                raise
            time.sleep(0.02)
            continue
        if int(envelope.get("observation_sequence", 0)) >= minimum_sequence or time.time() >= deadline:
            break
        time.sleep(0.02)
    assert envelope["schema_version"] == "1"
    assert envelope["session_id"] == session_id
    sequence = int(envelope["observation_sequence"])
    observation = envelope.get("observation", {})
    serialization_ms = int(observation.get("serializationMs", observation.get("serialization_ms", 0)))
    if cursor is not None:
        assert sequence > cursor, (cursor, sequence)
    parsed = pb.GameObservation()
    parsed.ParseFromString(protobuf_path.read_bytes())
    assert parsed.episode_id == session_id
    assert parsed.observation_sequence == sequence
    return {
        "sequence": sequence,
        "observed_at_unix_ms": int(envelope["observed_at_unix_ms"]),
        "protobuf_bytes": len(protobuf_path.read_bytes()),
        "serialization_ms": serialization_ms,
        "age_ms": max(0, int(time.time() * 1000) - int(envelope["observed_at_unix_ms"])),
    }


def wait_playing(stub, pb, session_id: str):
    for _ in range(120):
        state = stub.GetState(pb.StateRequest(session_id=session_id), timeout=5)
        if state.phase == "playing":
            return state
        if state.phase == "error":
            raise RuntimeError(f"{state.error_code}: {state.error_message}")
        time.sleep(0.25)
    raise TimeoutError(f"session {session_id} did not reach playing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--observation-dir", required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[7, 19, 42, 73, 101])
    parser.add_argument("--observation-count", type=int, default=10)
    parser.add_argument("--reconnect-delay-seconds", type=float, default=0.25)
    parser.add_argument("--evidence-name", default="phase1a-regression.json")
    args = parser.parse_args()

    import grpc

    project_root = Path(args.project_root).resolve()
    pb, rpc = load_proto(project_root)
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = rpc.RLBridgeStub(channel)
    fallback_root = Path(args.observation_dir).resolve()
    if args.observation_count < 3:
        parser.error("--observation-count must be at least 3")
    evidence = {"profile": "canonical", "seeds": [], "isolation": None, "cleanup": {}}

    sessions = []
    try:
        for seed in args.seeds:
            sid = stub.CreateSession(
                pb.CreateSessionRequest(
                    map_name="agenda.oramap",
                    bots="Multi1:agent-normal,Multi0:easy",
                    seed=seed,
                    player_faction="russia",
                    enemy_faction="england",
                    player_spawn=1,
                    enemy_spawn=2,
                ),
                timeout=30,
            ).session_id
            sessions.append(sid)
            state = wait_playing(stub, pb, sid)
            assert state.player_faction == "russia"
            assert state.enemy_faction == "england"
            assert state.player_spawn == 1
            assert state.enemy_spawn == 2

            stream = stub.StreamObservations(pb.StateRequest(session_id=sid), timeout=30)
            observations = [next(stream) for _ in range(args.observation_count)]
            assert all(a.observation_sequence < b.observation_sequence for a, b in zip(observations, observations[1:]))
            assert all(a.tick < b.tick for a, b in zip(observations, observations[1:]))
            fallback = read_fallback(fallback_root, sid, pb, minimum_sequence=observations[-1].observation_sequence)
            assert fallback["sequence"] >= observations[-1].observation_sequence
            assert fallback["age_ms"] <= 2000, fallback
            stream.cancel()

            time.sleep(max(0.0, args.reconnect_delay_seconds))
            after_disconnect = stub.GetState(pb.StateRequest(session_id=sid), timeout=5)
            assert after_disconnect.tick > observations[-1].tick
            reconnect_stream = stub.StreamObservations(pb.StateRequest(session_id=sid), timeout=30)
            reconnected = next(reconnect_stream)
            assert reconnected.observation_sequence > observations[-1].observation_sequence
            assert reconnected.tick > observations[-1].tick
            reconnect_stream.cancel()
            evidence["seeds"].append({
                "seed": seed,
                "session_id": sid,
                "ticks": [o.tick for o in observations],
                "sequences": [o.observation_sequence for o in observations],
                "fallback": fallback,
                "tick_after_disconnect": after_disconnect.tick,
                "reconnected_tick": reconnected.tick,
                "reconnected_sequence": reconnected.observation_sequence,
                "reconnect_delay_seconds": args.reconnect_delay_seconds,
            })

        a = stub.CreateSession(pb.CreateSessionRequest(
            map_name="agenda.oramap", bots="Multi1:agent-normal,Multi0:easy", seed=42,
            player_faction="russia", enemy_faction="england", player_spawn=1, enemy_spawn=2,
        ), timeout=30).session_id
        b = stub.CreateSession(pb.CreateSessionRequest(
            map_name="agenda.oramap", bots="Multi1:agent-normal,Multi0:easy", seed=73,
            player_faction="russia", enemy_faction="england", player_spawn=1, enemy_spawn=2,
        ), timeout=30).session_id
        sessions.extend([a, b])
        wait_playing(stub, pb, a)
        wait_playing(stub, pb, b)
        empty_state = stub.GetState(pb.StateRequest(), timeout=5)
        assert empty_state.phase == "no_bridge", empty_state
        evidence["isolation"] = {"session_a": a, "session_b": b, "empty_lookup_phase": empty_state.phase}
    finally:
        for sid in sessions:
            try:
                stub.DestroySession(pb.DestroySessionRequest(session_id=sid), timeout=15)
                # DestroySession is expected to be idempotent and to remove both
                # fallback files. Give the game thread a short chance to finish
                # its cleanup before recording evidence.
                deadline = time.time() + 2
                session_dir = fallback_root / sid
                while session_dir.exists() and time.time() < deadline:
                    time.sleep(0.05)
                evidence["cleanup"][sid] = not session_dir.exists()
            except Exception:
                evidence["cleanup"][sid] = False
        channel.close()

    assert evidence["cleanup"] and all(evidence["cleanup"].values()), evidence["cleanup"]

    freshness = sorted(item["fallback"]["age_ms"] for item in evidence["seeds"])
    serialization = sorted(item["fallback"]["serialization_ms"] for item in evidence["seeds"])
    p95_index = max(0, min(len(freshness) - 1, (len(freshness) * 95 + 99) // 100 - 1))
    evidence["metrics"] = {
        "freshness_samples_ms": freshness,
        "freshness_p95_ms": freshness[p95_index],
        "serializer_samples_ms": serialization,
        "serializer_p95_ms": serialization[p95_index],
        "freshness_gate_ms": 2000,
        "serializer_gate_ms": 20,
    }

    evidence_root = project_root.parent / "document" / "evidence" / "phase1a"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / args.evidence_name).write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
