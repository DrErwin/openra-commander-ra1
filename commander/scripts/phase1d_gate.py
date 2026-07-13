"""Phase 1d release gate for the read-only demo workspace.

The gate consumes committed Phase 1a/1b/1c evidence, builds the canonical
demo bundle, and writes a machine-readable release decision.  It deliberately
does not start OpenRA or issue a mission: 1d is the presentation and evidence
closure layer, while engine behavior is proven by the earlier gates.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

try:
    from phase1d_demo import build_bundle, build_state
except ImportError:  # pragma: no cover - supports direct package imports
    from commander.scripts.phase1d_demo import build_bundle, build_state


ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT.parent
EVIDENCE = PARENT / "document" / "evidence"
DEMO_EVIDENCE = EVIDENCE / "phase1d"
HTML = ROOT / "commander" / "demo" / "index.html"
CANONICAL = {
    "map": "agenda.oramap",
    "seed": 42,
    "player": "Multi1:agent-normal/russia/spawn1",
    "enemy": "Multi0:easy/england/spawn2",
}
SEEDS = [7, 19, 42, 73, 101]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_ok(value: dict[str, Any]) -> bool:
    return all(value.get(key) == expected for key, expected in CANONICAL.items())


def _phase1b_capture_ok(report: dict[str, Any]) -> bool:
    return bool(report.get("pass")) and report.get("seeds") == SEEDS and int(report.get("capture_successes", 0)) >= 4


def _phase1c_regression_ok(report: dict[str, Any]) -> bool:
    return bool(report.get("pass")) and report.get("seeds") == SEEDS and bool(report.get("checks", {}).get("all_runs_pass"))


def _autonomy_ok(report: dict[str, Any]) -> bool:
    return bool(report.get("pass")) and _canonical_ok(report.get("canonical", {})) and int(report.get("agent_actions_sent", 0)) == 0


def _timeline_ok(state: dict[str, Any]) -> bool:
    rows = state.get("timeline", [])
    return bool(rows) and all(row.get("within_replay") for row in rows) and all(
        int(row.get("replay_frame", -1)) == int(row.get("tick", 0)) // 3 for row in rows
    )


def _static_dashboard_ok() -> bool:
    source = HTML.read_text(encoding="utf-8")
    required = ("game-panel", "summary-panel", "mission-panel", "alert-panel", "timeline-panel", "checks-panel")
    forbidden = ("issue_mission", "cancel_mission", "mission-commands.jsonl", "method: 'POST'", "method: \"POST\"")
    ast.parse((ROOT / "commander" / "scripts" / "phase1d_demo.py").read_text(encoding="utf-8"))
    return all(token in source for token in required) and not any(token in source for token in forbidden)


def _write_final_regression(output: Path, phase1b: dict[str, Any], phase1c: dict[str, Any]) -> Path:
    report = {
        "schema_version": 1,
        "profile": "phase1d-final-regression",
        "seeds": SEEDS,
        "pass": _phase1b_capture_ok(phase1b) and _phase1c_regression_ok(phase1c),
        "phase1b": {
            "pass": phase1b.get("pass"),
            "capture_successes": phase1b.get("capture_successes"),
            "capture_success_gate": phase1b.get("capture_success_gate"),
            "control_plane_passes": phase1b.get("control_plane_passes"),
        },
        "phase1c": {
            "pass": phase1c.get("pass"),
            "all_runs_pass": phase1c.get("checks", {}).get("all_runs_pass"),
            "session_ids_unique": phase1c.get("checks", {}).get("session_ids_unique"),
            "all_replays_written": phase1c.get("checks", {}).get("all_replays_written"),
        },
        "evidence": [
            "../phase1b/phase1b-regression.json",
            "../phase1c/phase1c-live-mcp-regression.json",
        ],
    }
    path = output / "phase1d-final-regression.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_gate(output: Path = DEMO_EVIDENCE, novnc_url: str = "") -> dict[str, Any]:
    phase1a = _load(EVIDENCE / "phase1a" / "phase1a-autonomy-smoke.json")
    phase1b = _load(EVIDENCE / "phase1b" / "phase1b-regression.json")
    phase1b_canonical = _load(EVIDENCE / "phase1b" / "phase1b-mission-gate-final.json")
    phase1c = _load(EVIDENCE / "phase1c" / "phase1c-live-mcp.json")
    phase1c_regression = _load(EVIDENCE / "phase1c" / "phase1c-live-mcp-regression.json")
    output.mkdir(parents=True, exist_ok=True)
    state = build_bundle(EVIDENCE / "phase1c", output, novnc_url)
    regression_path = _write_final_regression(output, phase1b, phase1c_regression)
    rehearsal_path = output / "phase1d-canonical-rehearsal.json"
    rehearsal = _load(rehearsal_path) if rehearsal_path.is_file() else {}
    release_artifacts = [
        output / "phase1d-demo-state.json",
        output / "missions.yaml",
        output / "phase1d-replay-audit-timeline.json",
        output / "phase1d-demo-manifest.json",
        regression_path,
        rehearsal_path,
        output / "phase1d-demo-script.md",
        output.parent.parent / "phase1d-operator-guide.md",
        output.parent.parent / "phase1-release-notes.md",
    ]
    checks = {
        "canonical_profile": _canonical_ok(state.get("canonical", {})) and _canonical_ok(phase1a.get("canonical", {})) and _canonical_ok(phase1b_canonical.get("canonical", {})) and _canonical_ok(phase1c.get("canonical", {})),
        "phase1a_autonomy": _autonomy_ok(phase1a),
        "phase1b_capture_5_seed": _phase1b_capture_ok(phase1b),
        "phase1c_live_5_seed": _phase1c_regression_ok(phase1c_regression),
        "canonical_live_mcp": bool(phase1c.get("pass")),
        "replay_present": bool(state.get("replay", {}).get("found")) and (EVIDENCE / "phase1c" / "phase1c-live-mcp.orarep").is_file(),
        "replay_audit_timeline": _timeline_ok(state),
        "dashboard_read_only": _static_dashboard_ok(),
        "demo_state_schema": state.get("schema_version") == 1 and bool(state.get("session_id")) and state.get("viewer", {}).get("mode") in {"novnc", "replay-fallback"},
        "canonical_rehearsal": bool(rehearsal.get("pass")),
        "release_artifacts_present": all(path.is_file() for path in release_artifacts),
    }
    artifacts = [
        "phase1d-demo-state.json",
        "missions.yaml",
        "phase1d-replay-audit-timeline.json",
        "phase1d-demo-manifest.json",
        "phase1d-final-regression.json",
        "phase1d-canonical-rehearsal.json",
        "phase1d-demo-script.md",
        "../../phase1d-operator-guide.md",
        "../../phase1-release-notes.md",
    ]
    result = {
        "schema_version": 1,
        "profile": "phase1d-gate",
        "canonical": CANONICAL,
        "regression_seeds": SEEDS,
        "viewer_mode": state.get("viewer", {}).get("mode"),
        "novnc_configured": bool(novnc_url),
        "checks": checks,
        "artifacts": artifacts,
        "evidence_sources": [
            "../phase1a/phase1a-autonomy-smoke.json",
            "../phase1b/phase1b-mission-gate-final.json",
            "../phase1b/phase1b-regression.json",
            "../phase1c/phase1c-live-mcp.json",
            "../phase1c/phase1c-live-mcp-regression.json",
        ],
        "known_limitations": [
            "未配置 noVNC 时使用 replay/state fallback；dashboard 本身只读，不下达 mission。",
            "single-session 旧启动入口不承诺固定 faction/spawn；canonical 只走 multi-session CreateSession。",
            "A1.5 latest-observation 文件协议是 gRPC 异常回退与取证通道，不是路线切换。",
        ],
    }
    result["pass"] = all(checks.values())
    (output / "phase1d-gate.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Keep the generated regression path referenced even when a caller inspects
    # the return value before reading the artifact directory.
    result["final_regression_path"] = str(regression_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 1d presentation/release gate")
    parser.add_argument("--output-dir", type=Path, default=DEMO_EVIDENCE)
    parser.add_argument("--novnc-url", default="")
    args = parser.parse_args()
    result = run_gate(args.output_dir, args.novnc_url)
    print(json.dumps({"profile": result["profile"], "pass": result["pass"], "checks": result["checks"], "viewer_mode": result["viewer_mode"]}, ensure_ascii=False))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
