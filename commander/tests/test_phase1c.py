"""Contract and semantic tests for Phase 1c."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
package = types.ModuleType("openra_env")
package.__path__ = [str(ROOT / "openra_env")]
sys.modules.setdefault("openra_env", package)
server_package = types.ModuleType("openra_env.server")
server_package.__path__ = [str(ROOT / "openra_env" / "server")]
sys.modules.setdefault("openra_env.server", server_package)

from openra_env.phase1c import (  # noqa: E402
    AGENDA_POIS,
    AGENDA_ZONES,
    AlertStore,
    Phase1cFacade,
    SemanticAggregator,
    discover_session_id,
    load_low_level_events,
)
from openra_env.server.mission_client import MissionClient  # noqa: E402


def frame(session: str, tick: int, sequence: int, observed_at: int | None = None, *, enemy: bool = True) -> dict:
    return {
        "episode_id": session,
        "observation_sequence": sequence,
        "observed_at_unix_ms": observed_at or 2_000_000_000_000,
        "tick": tick,
        "map_info": {"map_name": "agenda.oramap", "width": 130, "height": 74},
        "explored_percent": 0.6,
        "economy": {"cash": 1000, "ore": 300, "power_provided": 100, "power_drained": 90, "harvester_count": 2},
        "military": {"army_value": 1000},
        "units": [{"actor_id": 1, "type": "e1", "owner": "Multi1", "cell_x": 55, "cell_y": 36, "can_attack": True}],
        "buildings": [{"actor_id": 10, "type": "proc", "owner": "Multi1", "cell_x": 55, "cell_y": 40}],
        "visible_enemies": ([{"actor_id": 99, "type": "e1", "owner": "Multi0", "cell_x": 57, "cell_y": 36, "can_attack": True}] if enemy else []),
        "visible_enemy_buildings": [],
        "production": [{"queue_type": "vehicle", "item": "1tnk"}],
    }


def test_agenda_registry_fog_and_public_view_never_exposes_actor_id():
    assert set(AGENDA_ZONES) == {"west_lane", "north_center", "center", "south_center", "east_lane"}
    assert len(AGENDA_POIS) == 8
    aggregator = SemanticAggregator("sess1")
    first = aggregator.ingest(frame("sess1", 0, 1))
    enemy_id = first["enemy"]["clusters"][0]["members"][0]
    assert "actor_id" not in json.dumps(first)
    alert_json = json.dumps(aggregator.alerts.read())
    assert "actor_id" not in alert_json and "target_actor_id" not in alert_json
    aggregator.ingest(frame("sess1", 10, 2), low_level_events=[{"session_id": "sess1", "event_id": "sess1:10:enemy_spotted:99",
                                                                   "type": "enemy_spotted", "actor_id": 99, "target_actor_id": 1, "tick": 10}])
    assert "sess1:10:enemy_spotted:99" not in json.dumps(aggregator.alerts.read())
    aggregator.ingest(frame("sess1", 300, 2, enemy=False))
    # Reappearance receives the same stable semantic id after a fog interval.
    third = aggregator.ingest(frame("sess1", 301, 3))
    assert third["enemy"]["clusters"][0]["members"][0] == enemy_id


def test_alert_cursor_mismatch_and_expiry():
    store = AlertStore("sess1", retention=2)
    store.append({"type": "low_cash", "message": "cash"}, tick=1)
    first = store.read()
    store.append({"type": "low_power", "message": "power"}, tick=2)
    store.append({"type": "zone_threat", "message": "threat"}, tick=3)
    assert store.read("v1:sess2:0")["session_mismatch"] is True
    assert store.read("v1:sess1:0")["cursor_expired"] is True
    assert store.read(first["cursor"])["alerts"][0]["type"] == "low_power"


def test_alert_paging_and_observation_freshness_errors():
    store = AlertStore("sess1", retention=256)
    for tick in range(120):
        store.append({"type": "production_complete", "message": str(tick)}, tick=tick)
    first = store.read("v1:sess1:0")
    second = store.read(first["cursor"])
    assert len(first["alerts"]) == 100 and len(second["alerts"]) == 20
    aggregator = SemanticAggregator("sess1")
    aggregator.ingest(frame("sess1", 1, 1, int(time.time() * 1000) - 2501))
    assert aggregator.battlefield()["stale"] is True
    aggregator.ingest(frame("sess1", 2, 2, int(time.time() * 1000) - 5001))
    try:
        aggregator.battlefield()
    except RuntimeError as exc:
        assert str(exc) == "observation_disconnected"
    else:
        raise AssertionError("disconnected frame was accepted")


def test_five_tools_and_mission_output_hides_internal_actor_id():
    with tempfile.TemporaryDirectory() as tmp:
        aggregator = SemanticAggregator("sess1")
        aggregator.ingest(frame("sess1", 25, 1))
        client = MissionClient("sess1", tmp)
        client.status_path.parent.mkdir(parents=True)
        client.status_path.write_text(json.dumps({
            "schema_version": 1, "session_id": "sess1", "last_applied_revision": 1,
            "generated_at_tick": 25, "missions": [{"id": "m1", "status": "in_progress",
            "assigned_units": [{"actor_id": 1, "semantic_id": "own_e1_1", "actor_type": "e1"}]}],
        }), encoding="utf-8")
        facade = Phase1cFacade("sess1", aggregator=aggregator, mission_client=client)
        assert facade.read_battlefield()["ok"]
        for filter_name in ("economy", "army", "buildings", "enemies", "map", "zone:center"):
            assert facade.read_battlefield(filter_name)["ok"]
        assert facade.get_alerts()["ok"]
        missions = facade.read_missions()
        assert missions["ok"] and "actor_id" not in json.dumps(missions)
        assert facade.read_missions(include_terminal=True, mission_id="m1")["ok"]
        issued = facade.issue_mission({"id": "m2", "type": "capture", "priority": "high", "ttl_ticks": 300,
                                       "target": "oil_center_west", "unit_type": "e6", "escort_units": 1})
        assert issued["ok"] and issued["data"]["status"] == "pending"
        duplicate = facade.issue_mission({"id": "m2", "type": "capture", "priority": "high", "ttl_ticks": 300,
                                          "target": "oil_center_west", "unit_type": "e6", "escort_units": 1})
        assert duplicate["ok"] and duplicate["data"]["revision"] == issued["data"]["revision"]
        cancelled = facade.cancel_mission("m2")
        assert cancelled["ok"]
        assert facade.cancel_mission("m2")["data"]["revision"] == cancelled["data"]["revision"]
        assert facade.read_battlefield("not-a-filter")["error"]["code"] == "INVALID_FILTER"
        assert facade.read_battlefield(extra=True)["error"]["code"] == "INVALID_SCHEMA"


def test_low_level_event_loader_tolerates_torn_tail():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "low-level-events.jsonl"
        path.write_text(json.dumps({"session_id": "sess1", "type": "enemy_spotted", "tick": 10}) + "\n{" , encoding="utf-8")
        events = load_low_level_events(path, session_id="sess1")
        assert len(events) == 1 and events[0]["type"] == "enemy_spotted"


def test_session_discovery_never_picks_arbitrary_directory():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sess1").mkdir()
        (root / "sess1" / "mission-commands.jsonl").write_text("", encoding="utf-8")
        assert discover_session_id(root) == "sess1"
        (root / "sess2").mkdir()
        (root / "sess2" / "mission-commands.jsonl").write_text("", encoding="utf-8")
        assert discover_session_id(root) is None


if __name__ == "__main__":
    for name, value in list(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("phase1c-tests-pass (6 tests)")
