"""Phase 1b canonical mission-control gate.

Runs one 180-second real-time Agenda session, issues production/capture and
attack/cancel commands through the JSONL control plane, and records runtime
status/audit evidence. The run does not send low-level AgentAction commands.
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
    server = types.ModuleType("openra_env.server")
    server.__path__ = [str(project_root / "openra_env" / "server")]
    sys.modules.setdefault("openra_env.server", server)
    from openra_env.generated import rl_bridge_pb2 as pb  # type: ignore
    from openra_env.generated import rl_bridge_pb2_grpc as rpc  # type: ignore
    from openra_env.server.mission_client import MissionClient  # type: ignore

    return pb, rpc, MissionClient


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
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--observation-dir", required=True)
    parser.add_argument("--duration-seconds", type=float, default=180.0)
    parser.add_argument("--evidence-name", default="phase1b-mission-gate.json")
    parser.add_argument("--keep-session", action="store_true", help="keep the session and runtime files for diagnostics")
    args = parser.parse_args()
    if args.duration_seconds < 30:
        parser.error("--duration-seconds must be at least 30 seconds")

    import grpc

    project_root = Path(args.project_root).resolve()
    pb, rpc, MissionClient = load_proto(project_root)
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = rpc.RLBridgeStub(channel)
    session_id = stub.CreateSession(
        pb.CreateSessionRequest(
            map_name="agenda.oramap",
            bots="Multi1:agent-normal,Multi0:easy",
            seed=args.seed,
            player_faction="russia",
            enemy_faction="england",
            player_spawn=1,
            enemy_spawn=2,
        ),
        timeout=120,
    ).session_id
    client = MissionClient(session_id, args.observation_dir, fsync=True)
    commands = {}
    observations = []
    stream = None
    status = {}
    audit_lines = []
    started = time.monotonic()
    last_state = None
    try:
        stream = stub.StreamObservations(pb.StateRequest(session_id=session_id), timeout=args.duration_seconds + 60)
        while time.monotonic() - started < args.duration_seconds:
            observation = next(stream)
            observations.append({
                "tick": observation.tick,
                "sequence": observation.observation_sequence,
                "units": len(observation.units),
                "buildings": len(observation.buildings),
                "production": len(observation.production),
                "visible_enemies": [u.actor_id for u in observation.visible_enemies],
            })
            tick = observation.tick
            if last_state is None or tick % 250 == 0:
                last_state = state_snapshot(stub.GetState(pb.StateRequest(session_id=session_id), timeout=10))

            if tick >= 50 and "build_barracks" not in commands:
                commands["build_barracks"] = client.issue_mission({
                    "id": "build_barracks",
                    "type": "build",
                    "priority": "high",
                    "ttl_ticks": 5000,
                    "actor_type": "barr",
                    "count": 1,
                }, observed_tick=tick)

            barracks_ready = any(b.type == "barr" for b in observation.buildings)
            if barracks_ready and "produce_e6" not in commands:
                commands["produce_e6"] = client.issue_mission({
                    "id": "produce_e6",
                    "type": "produce",
                    "priority": "medium",
                    "ttl_ticks": 4000,
                    "actor_type": "e6",
                    # Keep replacement engineers available so a blocked
                    # capture can reacquire after a unit death within the
                    # bounded 180-second run.
                    "count": 3,
                }, observed_tick=tick)

            # Keep the attack/cancel part of the gate independent from fog-of-war.
            # Agenda's player-start MCV is a stable semantic actor in this
            # canonical profile (enemy_actor_212); the mission API still
            # receives an ActorSemanticId, never a cell/zone target.
            if barracks_ready and "produce_attack_squad" not in commands:
                commands["produce_attack_squad"] = client.issue_mission({
                    "id": "produce_attack_squad",
                    "type": "produce",
                    "priority": "low",
                    "ttl_ticks": 5000,
                    "actor_type": "e1",
                    "count": 3,
                }, observed_tick=tick)

            visible_targets = list(observation.visible_enemies) + list(observation.visible_enemy_buildings)
            combat_units = sum(u.type in {"e1", "e2", "e3", "e4", "e7", "e8", "e9"} for u in observation.units)
            if tick >= 750 and combat_units >= 3 and "attack_visible" not in commands:
                target_id = visible_targets[0].actor_id if visible_targets else 212
                commands["attack_visible"] = client.issue_mission({
                    "id": "attack_visible",
                    "type": "attack",
                    "priority": "low",
                    "ttl_ticks": 3000,
                    "target": f"enemy_actor_{target_id}",
                    "force_units": 3,
                }, observed_tick=tick)

            engineer_ready = any(u.type == "e6" for u in observation.units)
            if engineer_ready and combat_units >= 3 and tick >= 1000 and "capture_oil_center_west" not in commands:
                commands["capture_oil_center_west"] = client.issue_mission({
                    "id": "capture_oil_center_west",
                    "type": "capture",
                    "priority": "high",
                    "ttl_ticks": 4000,
                    "target": "oil_west_edge",
                    "unit_type": "e6",
                    "escort_units": 3,
                }, observed_tick=tick)

            if tick >= 3000 and "attack_visible" in commands and "cancel_attack" not in commands:
                commands["cancel_attack"] = client.cancel_mission("attack_visible", reason="phase1b_gate_cancel")

            if observation.done:
                break
        # Read engine-owned mission files while the observation stream still
        # holds an observer lease. Otherwise the idle-TTL cleanup can remove
        # the session directory before evidence is collected.
        try:
            status = client.read_missions(retries=10)
        except Exception as exc:
            status = {"error": str(exc)}
        if client.audit_path.exists():
            for line in client.audit_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    audit_lines.append(json.loads(line))
    finally:
        if stream is not None:
            stream.cancel()
        try:
            last_state = state_snapshot(stub.GetState(pb.StateRequest(session_id=session_id), timeout=10))
        except grpc.RpcError:
            pass

    missions = {m.get("id"): m for m in status.get("missions", [])} if isinstance(status, dict) else {}
    terminal = {mid: data.get("status") in {"succeeded", "failed", "cancelled"} for mid, data in missions.items()}
    attack_audit = [a for a in audit_lines if a.get("mission_id") == "attack_visible"]
    capture_audit = [a for a in audit_lines if a.get("mission_id") == "capture_oil_center_west"]
    cancel_tick = next((a.get("tick") for a in attack_audit if a.get("event") == "cancelled"), None)
    release_ticks = [a.get("tick") for a in attack_audit if a.get("event") == "lease_released" and a.get("tick") is not None]
    cancel_release_ticks = [tick for tick in release_ticks if cancel_tick is not None and tick >= cancel_tick]
    attack_started = any(a.get("event") == "started" and a.get("reason") == "directed_squad_leased" for a in attack_audit)
    capture_succeeded = missions.get("capture_oil_center_west", {}).get("status") == "succeeded"
    accepted_started_latencies = {
        mid: data["started_at_tick"] - data["accepted_at_tick"]
        for mid, data in missions.items()
        if data.get("accepted_at_tick") is not None and data.get("started_at_tick") is not None
        and data.get("type") in {"produce", "capture", "attack"}
    }
    evidence = {
        "profile": "phase1b-mission-gate",
        "canonical": {
            "map": "agenda.oramap",
            "seed": args.seed,
            "player": "Multi1:agent-normal/russia/spawn1",
            "enemy": "Multi0:easy/england/spawn2",
        },
        "duration_seconds": args.duration_seconds,
        "session_id": session_id,
        "agent_actions_sent": 0,
        "observations": {
            "count": len(observations),
            "first_tick": observations[0]["tick"] if observations else None,
            "last_tick": observations[-1]["tick"] if observations else None,
            "monotonic": all(a["tick"] < b["tick"] for a, b in zip(observations, observations[1:])),
        },
        "resolved_configuration": last_state,
        "commands": {key: {"revision": value["revision"], "op": value["op"]} for key, value in commands.items()},
        "mission_status": missions,
        "audit": audit_lines,
        "terminal": terminal,
        "checks": {
            "session_match": bool(last_state and last_state["phase"] in {"playing", "game_over"}),
            "command_log_revision_monotonic": [commands[key]["revision"] for key in commands] == sorted(v["revision"] for v in commands.values()),
            "status_written_by_engine": client.status_path.exists(),
            "audit_written": len(audit_lines) >= len(commands),
            "cancel_recorded": any(a.get("event") == "cancelled" and a.get("mission_id") == "attack_visible" for a in audit_lines),
            "capture_started_or_blocked": any(a.get("mission_id") == "capture_oil_center_west" and a.get("event") in {"started", "blocked", "failed", "succeeded"} for a in audit_lines),
            "attack_started": attack_started,
            "capture_succeeded": capture_succeeded,
            "preemption_recorded": any(a.get("mission_id") == "attack_visible" and a.get("event") == "blocked" and a.get("reason") == "preempted" for a in attack_audit),
            "cancel_release_within_25_ticks": bool(cancel_release_ticks) and min(cancel_release_ticks) - cancel_tick <= 25,
            "start_latency_within_25_ticks": all(value <= 25 for value in accepted_started_latencies.values()),
            "terminal_status_complete": bool(missions) and all(terminal.values()),
        },
        "metrics": {
            "accepted_started_latency_ticks": accepted_started_latencies,
            "cancel_tick": cancel_tick,
            "cancel_release_tick": min(cancel_release_ticks) if cancel_release_ticks else None,
            "attack_audit_events": len(attack_audit),
            "capture_audit_events": len(capture_audit),
        },
    }
    evidence["pass"] = all(evidence["checks"].values()) and bool(commands)
    evidence_root = project_root.parent / "document" / "evidence" / "phase1b"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / args.evidence_name).write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        if not args.keep_session:
            stub.DestroySession(pb.DestroySessionRequest(session_id=session_id), timeout=30)
    finally:
        channel.close()
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
