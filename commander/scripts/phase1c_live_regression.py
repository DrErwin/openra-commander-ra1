"""Aggregate per-seed live MCP façade evidence."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    evidence_root = root.parent / "document" / "evidence" / "phase1c"
    seeds = [7, 19, 42, 73, 101]
    runs = []
    for seed in seeds:
        path = evidence_root / f"phase1c-live-mcp-seed-{seed}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        checks = data.get("checks", {})
        runs.append({"seed": seed, "session_id": data.get("session_id"), "pass": data.get("pass") is True,
                     "checks": checks, "audit_events": len(data.get("audit", [])),
                     "replay_size": data.get("replay", {}).get("size", 0)})
    evidence = {
        "profile": "phase1c-live-mcp-regression",
        "seeds": seeds,
        "runs": runs,
        "checks": {
            "all_runs_pass": all(run["pass"] for run in runs),
            "session_ids_unique": len({run["session_id"] for run in runs}) == len(runs),
            "all_audit_written": all(run["audit_events"] >= 4 for run in runs),
            "all_replays_written": all(run["replay_size"] > 0 for run in runs),
        },
    }
    evidence["pass"] = all(evidence["checks"].values())
    (evidence_root / "phase1c-live-mcp-regression.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

