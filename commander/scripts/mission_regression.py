"""Run the bounded Phase 1b mission gate for the fixed regression seeds.

This is intentionally a bounded autonomous run rather than a natural whole
game replay.  Each seed gets the same 180-second mission-control scenario and
the aggregate records status/audit evidence plus the capture terminal result.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--observation-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--duration-seconds", type=float, default=180.0)
    parser.add_argument("--seeds", nargs="+", type=int, default=[7, 19, 42, 73, 101])
    parser.add_argument("--evidence-name", default="phase1b-regression.json")
    args = parser.parse_args()
    if args.duration_seconds < 30:
        parser.error("--duration-seconds must be at least 30 seconds")

    project_root = Path(args.project_root).resolve()
    evidence_root = project_root.parent / "document" / "evidence" / "phase1b"
    evidence_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in args.seeds:
        name = f"phase1b-mission-seed-{seed}.json"
        command = [
            sys.executable,
            str(project_root / "commander" / "scripts" / "mission_gate.py"),
            "--project-root", str(project_root),
            "--observation-dir", args.observation_dir,
            "--host", args.host,
            "--port", str(args.port),
            "--seed", str(seed),
            "--duration-seconds", str(args.duration_seconds),
            "--evidence-name", name,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        path = evidence_root / name
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            row = {"seed": seed, "pass": False, "error": "mission gate produced no JSON evidence"}
        row["seed"] = seed
        row["exit_code"] = result.returncode
        rows.append(row)

    capture_successes = sum(
        next((m.get("status") == "succeeded" for m in row.get("mission_status", {}).values() if m.get("type") == "capture"), False)
        for row in rows
    )
    aggregate = {
        "profile": "phase1b-regression",
        "seeds": args.seeds,
        "duration_seconds_per_seed": args.duration_seconds,
        "results": rows,
        "capture_successes": capture_successes,
        "capture_success_gate": max(0, len(args.seeds) - 1),
        "control_plane_passes": sum(bool(row.get("pass")) for row in rows),
        "pass": capture_successes >= max(0, len(args.seeds) - 1) and all(bool(row.get("pass")) for row in rows),
    }
    (evidence_root / args.evidence_name).write_text(
        json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(aggregate, indent=2, ensure_ascii=False))
    return 0 if aggregate["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
