import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_observation_envelope_schema_is_closed_2020_12():
    schema = json.loads(
        (ROOT.parent / "document" / "technical-design" / "contracts" / "observation-envelope.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "1"


def test_phase1_formal_contracts_are_closed_2020_12():
    contracts = ROOT.parent / "document" / "technical-design" / "contracts"
    for name in ("mission-event.schema.json", "mission-status.schema.json", "alert.schema.json"):
        schema = json.loads((contracts / name).read_text(encoding="utf-8"))
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["additionalProperties"] is False


def test_proto_mirrors_are_identical():
    root_proto = (ROOT / "proto" / "rl_bridge.proto").read_text(encoding="utf-8")
    nested_proto = (
        ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "Player" / "Protos" / "rl_bridge.proto"
    ).read_text(encoding="utf-8")
    assert root_proto == nested_proto
    for field in ("observation_sequence", "observed_at_unix_ms", "serialization_ms", "schema_version", "player_spawn", "enemy_spawn"):
        assert field in root_proto


def test_agent_normal_isolation_contract():
    yaml = (ROOT / "OpenRA" / "mods" / "ra" / "rules" / "rl-bot.yaml").read_text(encoding="utf-8")
    assert "Type: agent-normal" in yaml
    assert "Condition: enable-agent-steering" in yaml
    assert "Bots: agent-normal" in yaml
    assert "Bots: easy" not in yaml


def test_steering_module_is_observe_only():
    source = (
        ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "Player" / "Commander" / "SteeringModule.cs"
    ).read_text(encoding="utf-8")
    assert "QueueOrder" not in source
    assert "IssueOrder" not in source
    assert re.search(r"\b(?:Order|OrderPacket|OrderManager)\b", source) is None


def test_multisession_replay_is_per_session_and_opt_in():
    manager = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "Player" / "RLSessionManager.cs").read_text(
        encoding="utf-8"
    )
    connection = (ROOT / "OpenRA" / "OpenRA.Game" / "Network" / "Connection.cs").read_text(encoding="utf-8")
    assert 'Environment.GetEnvironmentVariable("RL_RECORD_REPLAYS")' in manager
    assert "new ReplayRecorder" in manager
    assert "new EchoConnection(replayRecorder)" in manager
    assert "Recorder?.Receive" in connection
    assert manager.index("ValidateSessionConfiguration") < manager.index("new ReplayRecorder")


def test_autonomy_gate_is_no_action_bounded_smoke():
    source = (ROOT / "commander" / "scripts" / "autonomy_gate.py").read_text(encoding="utf-8")
    assert "FastAdvance(" not in source
    assert "AgentAction(" not in source
    assert '"agent_actions_sent": 0' in source
    assert '"activity_signal"' in source


def test_python_sources_parse():
    for path in (
        ROOT / "openra_env" / "config.py",
        ROOT / "openra_env" / "server" / "bridge_client.py",
        ROOT / "openra_env" / "server" / "openra_environment.py",
        ROOT / "openra_env" / "server" / "openra_process.py",
        ROOT / "commander" / "scripts" / "regression.py",
        ROOT / "commander" / "scripts" / "baseline.py",
        ROOT / "commander" / "scripts" / "replay_gate.py",
        ROOT / "commander" / "scripts" / "steering_gate.py",
        ROOT / "commander" / "scripts" / "autonomy_gate.py",
        ROOT / "openra_env" / "server" / "mission_client.py",
    ):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_phase1b_runtime_surface_exists():
    mission = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "Player" / "Commander" / "MissionControl.cs").read_text(encoding="utf-8")
    squad = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "BotModules" / "SquadManagerBotModule.cs").read_text(encoding="utf-8")
    capture = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "BotModules" / "CaptureManagerBotModule.cs").read_text(encoding="utf-8")
    assert "IBotMissionCoordinator" in mission
    assert "mission-commands.jsonl" in mission and "mission-status.json" in mission and "mission-audit.jsonl" in mission
    assert "TryLeaseDirectedSquad" in squad and "ReleaseDirectedSquad" in squad
    assert "RequestCapture" in capture and "CancelCapture" in capture
    assert "priority" in mission and "preempted" in mission and "revision_gap" in mission


def test_phase1c_semantic_boundary_and_event_detector():
    semantic = (ROOT / "openra_env" / "phase1c.py").read_text(encoding="utf-8")
    mcp = (ROOT / "openra_env" / "phase1c_mcp.py").read_text(encoding="utf-8")
    detector = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "Player" / "Commander" / "EngineEventDetector.cs").read_text(encoding="utf-8")
    yaml = (ROOT / "OpenRA" / "mods" / "ra" / "rules" / "rl-bot.yaml").read_text(encoding="utf-8")
    assert "class SemanticRegistry" in semantic and "class AlertStore" in semantic
    assert "actor_id" in semantic  # internal conversion is present, but public missions strip it below
    assert mcp.count("@server.tool()") == 5
    assert "class EngineEventDetector" in detector and "low-level-events.jsonl" in detector
    assert "File.Delete(eventPath)" in detector
    for event_type in ("under_attack", "unit_destroyed", "own_building_destroyed", "production_complete", "enemy_spotted", "building_discovered"):
        assert event_type in detector
    assert "QueueOrder" not in detector and "IssueOrder" not in detector
    assert "EngineEventDetector:" in yaml and "RequiresCondition: enable-agent-steering" in yaml


def test_phase1c_python_sources_parse():
    for path in (ROOT / "openra_env" / "phase1c.py", ROOT / "openra_env" / "phase1c_mcp.py", ROOT / "commander" / "scripts" / "phase1c_gate.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"contract-tests-pass ({len(tests)} tests)")
