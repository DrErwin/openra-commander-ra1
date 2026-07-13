"""Cold-start rehearsal for the Phase 1d read-only dashboard."""

from __future__ import annotations

import argparse
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from phase1d_demo import DEFAULT_DEMO, DEFAULT_EVIDENCE, _Handler, build_bundle


ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT.parent
OUTPUT = PARENT / "document" / "evidence" / "phase1d"


def _get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - localhost only
        body = response.read()
        return response.status, body, response.headers.get("Content-Type", "")


def run_rehearsal(output: Path = OUTPUT, novnc_url: str = "") -> dict:
    state = build_bundle(DEFAULT_EVIDENCE, output, novnc_url)
    handler = type("RehearsalHandler", (_Handler,), {"evidence_root": DEFAULT_EVIDENCE, "demo_dir": DEFAULT_DEMO, "novnc_url": novnc_url})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        root_status, html, html_type = _get(base + "/")
        api_status, payload, api_type = _get(base + "/api/state")
        api_state = json.loads(payload)
        required_panels = ["game-panel", "summary-panel", "mission-panel", "alert-panel", "timeline-panel", "checks-panel"]
        checks = {
            "cold_start_bundle": bool(state.get("session_id")) and (output / "phase1d-demo-manifest.json").is_file(),
            "dashboard_http_200": root_status == 200 and "text/html" in html_type,
            "api_state_http_200": api_status == 200 and "application/json" in api_type,
            "canonical_state_served": api_state.get("canonical") == state.get("canonical") and api_state.get("session_id") == state.get("session_id"),
            "required_panels_present": all(panel in html.decode("utf-8") for panel in required_panels),
            "read_only_surface": "issue_mission" not in html.decode("utf-8") and "cancel_mission" not in html.decode("utf-8"),
            "timeline_served": bool(api_state.get("timeline")) and all(row.get("within_replay") for row in api_state["timeline"]),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result = {
        "schema_version": 1,
        "profile": "phase1d-canonical-rehearsal",
        "canonical": state.get("canonical"),
        "session_id": state.get("session_id"),
        "viewer_mode": state.get("viewer", {}).get("mode"),
        "novnc_configured": bool(novnc_url),
        "checks": checks,
        "pass": all(checks.values()),
        "served_routes": ["/", "/api/state"],
        "read_only": True,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "phase1d-canonical-rehearsal.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local cold-start rehearsal for the Phase 1d demo")
    parser.add_argument("--novnc-url", default="")
    args = parser.parse_args()
    result = run_rehearsal(novnc_url=args.novnc_url)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
