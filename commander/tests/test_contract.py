import ast
import json
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


def test_python_sources_parse():
    for path in (
        ROOT / "openra_env" / "config.py",
        ROOT / "openra_env" / "server" / "bridge_client.py",
        ROOT / "openra_env" / "server" / "openra_environment.py",
        ROOT / "openra_env" / "server" / "openra_process.py",
        ROOT / "commander" / "scripts" / "regression.py",
        ROOT / "commander" / "scripts" / "baseline.py",
    ):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
