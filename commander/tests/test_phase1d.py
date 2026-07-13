"""Tests for the Phase 1d read-only demo bundle."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "commander" / "scripts"))

from phase1d_demo import build_bundle, build_state  # noqa: E402


EVIDENCE = ROOT.parent / "document" / "evidence" / "phase1c"
HTML = ROOT / "commander" / "demo" / "index.html"


def test_canonical_state_and_checks():
    state = build_state(EVIDENCE)
    assert state["canonical"] == {
        "map": "agenda.oramap",
        "seed": 42,
        "player": "Multi1:agent-normal/russia/spawn1",
        "enemy": "Multi0:easy/england/spawn2",
    }
    assert state["session_id"]
    assert state["checks"]["five_seed_regression"]
    assert state["checks"]["replay_audit_timeline"]


def test_bundle_contains_missions_and_timeline():
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp)
        state = build_bundle(EVIDENCE, output)
        for name in ("phase1d-demo-state.json", "missions.yaml", "phase1d-replay-audit-timeline.json", "phase1d-demo-manifest.json"):
            assert (output / name).is_file()
        yaml = (output / "missions.yaml").read_text(encoding="utf-8")
        assert "mcp_capture_oil_center_west" in yaml and "cancelled" in yaml
        timeline = json.loads((output / "phase1d-replay-audit-timeline.json").read_text(encoding="utf-8"))
        assert timeline["session_id"] == state["session_id"]
        assert timeline["timeline"] and all(row["within_replay"] for row in timeline["timeline"])


def test_dashboard_panels_are_read_only():
    html = HTML.read_text(encoding="utf-8")
    for panel in ("game-panel", "summary-panel", "mission-panel", "alert-panel", "timeline-panel", "checks-panel"):
        assert f'id="{panel}"' in html
    for forbidden in ("issue_mission", "cancel_mission", "mission-commands.jsonl", "method: 'POST'"):
        assert forbidden not in html


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"phase1d-tests-pass ({len(tests)} tests)")
