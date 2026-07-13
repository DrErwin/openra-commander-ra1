"""Dedicated Phase 1c semantic MCP server.

Unlike the legacy OpenRA MCP server, this façade exposes exactly five tools
and never forwards raw actor ids or protobuf messages to the Agent.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from openra_env.phase1c import Phase1cFacade, discover_session_id


def create_server(facade: Phase1cFacade | None = None) -> FastMCP:
    session_id = os.environ.get("RL_SESSION_ID", "")
    if facade is None:
        if not session_id:
            session_id = discover_session_id()
        if not session_id:
            raise RuntimeError("NO_ACTIVE_SESSION: set RL_SESSION_ID or keep exactly one runtime session")
        facade = Phase1cFacade(session_id)

    server = FastMCP("openra-rl-phase1c", instructions="Semantic Agenda battlefield and mission control")

    @server.tool()
    def read_battlefield(filter: str | None = None) -> dict[str, Any]:
        """Read the semantic Agenda battlefield; filter is all/economy/army/buildings/enemies/map/zone:<id>."""
        return facade.read_battlefield(filter)

    @server.tool()
    def get_alerts(cursor: str | None = None) -> dict[str, Any]:
        """Read derived and engine alerts using an opaque session cursor."""
        return facade.get_alerts(cursor)

    @server.tool()
    def read_missions(include_terminal: bool = False, mission_id: str | None = None) -> dict[str, Any]:
        """Read mission status without exposing internal actor ids."""
        return facade.read_missions(include_terminal, mission_id)

    @server.tool()
    def issue_mission(mission: dict[str, Any]) -> dict[str, Any]:
        """Persist a Phase 1 mission event (attack/capture/produce/build)."""
        return facade.issue_mission(mission)

    @server.tool()
    def cancel_mission(mission_id: str, reason: str = "operator_cancel") -> dict[str, Any]:
        """Persist an idempotent mission cancellation request."""
        return facade.cancel_mission(mission_id, reason)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the five-tool Phase 1c semantic MCP server")
    parser.add_argument("--transport", default="stdio", choices=("stdio", "sse"))
    args = parser.parse_args()
    create_server().run(transport=args.transport)


if __name__ == "__main__":
    main()
