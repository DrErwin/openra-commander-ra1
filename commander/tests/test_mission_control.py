"""Protocol and arbitration-shape checks for Phase 1b mission control."""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
package = types.ModuleType("openra_env")
package.__path__ = [str(ROOT / "openra_env")]
sys.modules.setdefault("openra_env", package)
server_package = types.ModuleType("openra_env.server")
server_package.__path__ = [str(ROOT / "openra_env" / "server")]
sys.modules.setdefault("openra_env.server", server_package)

from openra_env.server.mission_client import MissionClient, MissionProtocolError


def test_issue_cancel_revision_and_torn_tail():
    with tempfile.TemporaryDirectory() as tmp:
        client = MissionClient("sess1", tmp)
        issue = client.issue_mission(
            {"id": "attack1", "type": "attack", "priority": "low", "ttl_ticks": 300, "target": "enemy_actor_99"},
            observed_tick=10,
        )
        assert issue["revision"] == 1
        client.commands_path.write_text(client.commands_path.read_text(encoding="utf-8") + '{"revision":', encoding="utf-8")
        assert client._next_revision() == 2
        client.commands_path.write_text(client.commands_path.read_text(encoding="utf-8").split("{\"revision\":")[0], encoding="utf-8")
        cancel = client.cancel_mission("attack1")
        assert cancel["revision"] == 2
        assert [json.loads(line)["revision"] for line in client.commands_path.read_text(encoding="utf-8").splitlines()] == [1, 2]


def test_mission_input_rejects_micro_and_unknown_types():
    with tempfile.TemporaryDirectory() as tmp:
        client = MissionClient("sess1", tmp)
        for mission in (
            {"id": "m1", "type": "attack", "priority": "low", "ttl_ticks": 300, "target": "enemy_actor_1", "target_x": 3},
            {"id": "m2", "type": "defend", "priority": "low", "ttl_ticks": 300, "zone": "north"},
        ):
            try:
                client.issue_mission(mission)
            except MissionProtocolError as exc:
                assert exc.code in {"INVALID_SCHEMA", "MISSION_TYPE_NOT_ENABLED"}
            else:
                raise AssertionError("invalid mission was accepted")


def test_status_is_session_scoped_and_atomic_shape():
    with tempfile.TemporaryDirectory() as tmp:
        client = MissionClient("sess1", tmp)
        client.session_dir.mkdir(parents=True)
        client.status_path.write_text(
            json.dumps({"schema_version": 1, "session_id": "sess2", "last_applied_revision": 0, "generated_at_tick": 1, "missions": []}),
            encoding="utf-8",
        )
        try:
            client.read_missions()
        except MissionProtocolError as exc:
            assert exc.code == "SESSION_MISMATCH"
        else:
            raise AssertionError("cross-session status was accepted")


def test_lease_and_save_load_surface_is_present():
    mission_source = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "Player" / "Commander" / "MissionControl.cs").read_text(encoding="utf-8")
    squad_source = (ROOT / "OpenRA" / "OpenRA.Mods.Common" / "Traits" / "BotModules" / "SquadManagerBotModule.cs").read_text(encoding="utf-8")
    assert "IGameSaveTraitData" in mission_source
    assert "SavedMission" in mission_source
    assert "save_load_reacquire" in mission_source
    assert "activeUnits.UnionWith(candidates)" in squad_source
    assert "existingSquad.Units.RemoveWhere(fallback.Contains)" in squad_source


if __name__ == "__main__":
    for name, value in list(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("mission-control-tests-pass (4 tests)")
