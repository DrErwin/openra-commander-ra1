"""Pure tests for the Phase 1d live operator entry point."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "commander" / "scripts" / "live_operator.py"


def _module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("phase1d_live_operator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_natural_language_smoke_maps_to_semantic_missions():
    live = _module()
    capture = live._mission_from_text("抢中间偏西的油井")
    assert capture["type"] == "capture"
    assert capture["target"] == "oil_center_west"
    assert capture["unit_type"] == "e6"

    produce = live._mission_from_text("生产 e6 工程师")
    assert produce["type"] == "produce"
    assert produce["actor_type"] == "e6"


def test_metadata_writer_is_atomic_and_json():
    live = _module()
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "live-session.json"
        live._write_json(path, {"schema_version": 1, "status": "playing"})
        assert json.loads(path.read_text(encoding="utf-8"))["status"] == "playing"
        assert not list(Path(temp).glob("*.tmp"))


def test_status_does_not_write_supervisor_metadata():
    live = _module()
    metadata = {"schema_version": 1, "status": "playing"}
    output = []

    class State:
        phase = "playing"
        tick = 25
        player_faction = "england"
        enemy_faction = "ukraine"
        player_spawn = 1
        enemy_spawn = 2

    class Bridge:
        def get_state(self):
            return State()

        def close(self):
            pass

    live._metadata_or_die = lambda: dict(metadata)
    live._facade = lambda _: (object(), Bridge())
    live._print_json = output.append
    live._write_json = lambda *_: (_ for _ in ()).throw(AssertionError("status must not write metadata"))

    assert live._status(None) == 0
    assert output[0]["tick"] == 25
    assert output[0]["status"] == "playing"


def test_safe_runtime_root_rejects_outside_workspace():
    live = _module()
    with tempfile.TemporaryDirectory() as temp:
        outside = Path(temp) / "outside"
        try:
            live._safe_runtime_root(outside)
        except SystemExit as exc:
            assert "runtime root" in str(exc)
        else:
            raise AssertionError("outside runtime root must be rejected")


if __name__ == "__main__":
    test_natural_language_smoke_maps_to_semantic_missions()
    test_metadata_writer_is_atomic_and_json()
    test_status_does_not_write_supervisor_metadata()
    test_safe_runtime_root_rejects_outside_workspace()
    print("test_live_operator: 4 passed")
