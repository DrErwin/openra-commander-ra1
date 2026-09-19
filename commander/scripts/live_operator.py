"""Phase 1d live operator for a visible OpenRA session.

This is the small process supervisor that was missing from the replay-only
demo.  It starts the single-session game with ``headless=False``, keeps the
ExternalBotBridge stream open (which unpauses the game), and exposes the same
semantic Phase 1c façade to an MCP client.  The operator process owns the game
child; the MCP process only reads observations and appends mission events.

Typical use from the repository root::

    python commander/scripts/live_operator.py start
    python commander/scripts/live_operator.py status
    python commander/scripts/live_operator.py observe
    python commander/scripts/live_operator.py turn "抢中间偏西的油井"
    python commander/scripts/live_operator.py mcp
    python commander/scripts/live_operator.py stop

The formal Agent boundary is the five-tool MCP server.  ``turn`` is a bounded
smoke-test convenience for a human operator; it is deliberately not an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import subprocess
import sys
import threading
import time
import types
import uuid
from pathlib import Path
from typing import Any, Iterable


SCRIPT = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT.parents[2]
RUNTIME_ROOT = PROJECT_ROOT / "commander" / "runtime" / "live"
METADATA_PATH = RUNTIME_ROOT / "live-session.json"
STOP_PATH = RUNTIME_ROOT / "live-stop"
LOG_PATH = PROJECT_ROOT / "commander" / "evidence" / "live-operator.log"


def _imports():
    """Import the package without requiring an editable install."""
    package = types.ModuleType("openra_env")
    package.__path__ = [str(PROJECT_ROOT / "openra_env")]
    sys.modules.setdefault("openra_env", package)
    generated = types.ModuleType("openra_env.generated")
    generated.__path__ = [str(PROJECT_ROOT / "openra_env" / "generated")]
    sys.modules.setdefault("openra_env.generated", generated)
    server = types.ModuleType("openra_env.server")
    server.__path__ = [str(PROJECT_ROOT / "openra_env" / "server")]
    sys.modules.setdefault("openra_env.server", server)
    from openra_env.phase1c import Phase1cFacade
    from openra_env.server.bridge_client import BridgeClient
    from openra_env.server.mission_client import MissionClient
    from openra_env.server.openra_process import OpenRAConfig, OpenRAProcessManager
    from openra_env.generated import rl_bridge_pb2 as pb
    from openra_env.generated import rl_bridge_pb2_grpc as rpc

    return Phase1cFacade, BridgeClient, MissionClient, OpenRAConfig, OpenRAProcessManager, pb, rpc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        temp.unlink(missing_ok=True)


def _read_json(path: Path = METADATA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def _safe_runtime_root(path: Path) -> Path:
    resolved = path.resolve()
    expected = (PROJECT_ROOT / "commander" / "runtime").resolve()
    if expected not in resolved.parents and resolved != expected:
        raise SystemExit(f"runtime root must stay under {expected}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _request_stream(stop: threading.Event, pb: Any) -> Iterable[Any]:
    # One no-op is enough to call ExternalBotBridge.OnAgentConnected; further
    # no-ops prevent a client-side idle timeout while the user is playing.
    yield pb.AgentAction()
    while not stop.wait(1.0):
        yield pb.AgentAction()


def _keepalive(host: str, port: int, stop: threading.Event, pb: Any, rpc: Any,
               result: dict[str, Any]) -> None:
    import grpc

    channel = grpc.insecure_channel(f"{host}:{port}")
    try:
        stub = rpc.RLBridgeStub(channel)
        responses = stub.GameSession(_request_stream(stop, pb))
        for observation in responses:
            result["last_tick"] = int(observation.tick)
            result["last_sequence"] = int(observation.observation_sequence)
            result["last_observed_at_unix_ms"] = int(observation.observed_at_unix_ms)
            if observation.done:
                result["game_over"] = True
                stop.set()
                break
    except Exception as exc:  # gRPC reports normal shutdown as RpcError.
        if not stop.is_set():
            result["error"] = f"{type(exc).__name__}: {exc}"
            stop.set()
    finally:
        channel.close()


def _wait_for_bridge(manager: Any, bridge: Any, retries: int, interval: float) -> Any | None:
    """Wait for the in-game gRPC service without killing a slow map load."""
    for _ in range(max(1, retries)):
        if not manager.is_alive():
            return None
        try:
            if not bridge._connected:
                bridge.connect()
            state = bridge.get_state()
            if state.phase == "playing":
                return state
            if state.phase == "error":
                return state
        except Exception:
            pass
        time.sleep(max(0.05, interval))
    return None


def _supervisor(args: argparse.Namespace) -> int:
    Phase1cFacade, BridgeClient, MissionClient, OpenRAConfig, OpenRAProcessManager, pb, rpc = _imports()
    runtime_root = _safe_runtime_root(Path(args.runtime_root))
    metadata_path = runtime_root / "live-session.json"
    stop_path = runtime_root / "live-stop"
    log_path = Path(args.log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if stop_path.exists():
        stop_path.unlink()
    os.environ.update({
        "RL_OBSERVATION_DIR": str(runtime_root),
        "RL_MISSION_DIR": str(runtime_root),
        "RL_EVENT_DIR": str(runtime_root),
    })
    # OpenRA writes logs/config/replays under %APPDATA%.  The managed desktop
    # profile may be read-only, so keep this session's support files beside its
    # observation and mission protocol.
    support_root = runtime_root / "openra-user"
    support_root.mkdir(parents=True, exist_ok=True)
    # A previous headless run can leave ``Game: Platform: Null`` in the
    # shared support directory.  If a visible run reuses that directory,
    # OpenRA starts successfully but creates no desktop window, so VNC only
    # shows the user's existing desktop.  Remove that stale two-line block
    # for visible sessions; headless sessions keep their explicit setting.
    if not args.headless:
        settings_path = support_root / "settings.yaml"
        if settings_path.exists():
            settings_text = settings_path.read_text(encoding="utf-8")
            cleaned = re.sub(
                r"(?m)^Game:\r?\n\tPlatform:\s*Null\r?\n(?:\r?\n)?",
                "",
                settings_text,
            )
            if cleaned != settings_text:
                settings_path.write_text(cleaned, encoding="utf-8")
    os.environ["APPDATA"] = str(support_root)
    os.environ["LOCALAPPDATA"] = str(support_root)

    config = OpenRAConfig(
        openra_path=str(Path(args.openra_path).resolve()),
        map_name=args.map,
        grpc_port=args.port,
        bot_type="easy",
        rl_bot_type="agent-normal",
        rl_slot="Multi1",
        ai_slot="Multi0",
        observation_dir=str(runtime_root),
        headless=bool(args.headless),
        record_replays=True,
        multi_session=False,
        extra_args={"Engine.SupportDir": str(support_root),
                    "Engine.ModSearchPaths": str(Path(args.openra_path).resolve() / "mods")},
    )
    manager = OpenRAProcessManager(config)
    keepalive_stop = threading.Event()
    keepalive_result: dict[str, Any] = {}
    bridge = None
    channel_thread = None
    pid = manager.launch()
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "profile": "phase1d-live-visible",
        "status": "starting",
        "game_pid": pid,
        "supervisor_pid": os.getpid(),
        "grpc_host": args.host,
        "grpc_port": args.port,
        "runtime_root": str(runtime_root),
        "map": args.map,
        "player": "Multi1:agent-normal",
        "enemy": "Multi0:easy",
        "visible": not bool(args.headless),
        "headless": bool(args.headless),
        "faction_spawn_note": "single-session launcher does not guarantee fixed faction/spawn; use multi-session for canonical fixed values",
        "started_at_unix_ms": int(time.time() * 1000),
    }
    _write_json(metadata_path, metadata)

    try:
        bridge = BridgeClient(host=args.host, port=args.port, observation_dir=str(runtime_root), timeout_s=1.5)
        state = _wait_for_bridge(manager, bridge, args.ready_retries, 0.5)
        if state is None or state.phase != "playing":
            metadata.update({"status": "error", "error_code": "BRIDGE_NOT_READY",
                             "error_message": (manager.get_stdout()[-3000:] + "\n" + manager.get_stderr()[-3000:]).strip()})
            _write_json(metadata_path, metadata)
            return 2
        metadata.update({
            "status": state.phase,
            "session_id": state.episode_id,
            "tick": int(state.tick),
            "player_faction": state.player_faction,
            "enemy_faction": state.enemy_faction,
            "player_spawn": int(state.player_spawn),
            "enemy_spawn": int(state.enemy_spawn),
        })
        _write_json(metadata_path, metadata)

        # The bridge deliberately pauses a single-session game until this
        # stream connects. Start it before exposing the session to the Agent.
        channel_thread = threading.Thread(
            target=_keepalive,
            args=(args.host, args.port, keepalive_stop, pb, rpc, keepalive_result),
            daemon=True,
            name="openra-live-keepalive",
        )
        channel_thread.start()
        time.sleep(0.2)
        try:
            first = bridge.read_observation(state.episode_id, timeout_s=2)
            metadata.update({"status": "playing", "tick": int(first.observation.tick),
                             "observation_sequence": first.sequence,
                             "observed_at_unix_ms": first.observed_at_unix_ms})
            _write_json(metadata_path, metadata)
        except Exception as exc:
            metadata.update({"status": "error", "error_code": "OBSERVATION_NOT_READY", "error_message": str(exc)})
            _write_json(metadata_path, metadata)
            return 2

        while manager.is_alive() and not stop_path.exists() and not keepalive_stop.is_set():
            state = bridge.get_state()
            metadata.update({"status": state.phase, "tick": int(state.tick),
                             "last_sequence": keepalive_result.get("last_sequence"),
                             "last_keepalive_error": keepalive_result.get("error")})
            if state.phase in {"game_over", "error"}:
                metadata["status"] = state.phase
                if state.error_code:
                    metadata.update({"error_code": state.error_code, "error_message": state.error_message})
                _write_json(metadata_path, metadata)
                break
            _write_json(metadata_path, metadata)
            time.sleep(0.5)
    except Exception as exc:
        metadata.update({"status": "error", "error_code": "SUPERVISOR_ERROR", "error_message": str(exc)})
        _write_json(metadata_path, metadata)
        return 2
    finally:
        keepalive_stop.set()
        if channel_thread is not None:
            channel_thread.join(timeout=2)
        if bridge is not None:
            bridge.close()
        exit_code = manager.kill(timeout=3)
        final_status = metadata.get("status")
        if final_status not in {"game_over", "error"}:
            final_status = "stopped"
        metadata.update({"status": final_status, "stopped_at_unix_ms": int(time.time() * 1000),
                         "game_exit_code": exit_code, "keepalive": keepalive_result})
        if stop_path.exists():
            stop_path.unlink()
        _write_json(metadata_path, metadata)
    return 0


def _metadata_or_die() -> dict[str, Any]:
    if not METADATA_PATH.exists():
        raise SystemExit("没有活动的可见会话，请先运行 start")
    return _read_json(METADATA_PATH)


def _facade(metadata: dict[str, Any]):
    Phase1cFacade, BridgeClient, MissionClient, *_ = _imports()
    session_id = str(metadata.get("session_id", ""))
    if not session_id:
        raise SystemExit("live-session.json 尚未包含 session_id；游戏仍在启动或已失败")
    runtime_root = Path(metadata["runtime_root"])
    os.environ.update({"RL_OBSERVATION_DIR": str(runtime_root), "RL_MISSION_DIR": str(runtime_root), "RL_EVENT_DIR": str(runtime_root),
                       "RL_SESSION_ID": session_id})
    bridge = BridgeClient(host=metadata.get("grpc_host", "127.0.0.1"), port=int(metadata.get("grpc_port", 9999)),
                          session_id=session_id, observation_dir=str(runtime_root), timeout_s=5)
    bridge.connect()
    missions = MissionClient(session_id, runtime_root, fsync=True)
    return Phase1cFacade(session_id, runtime_root=runtime_root, mission_client=missions, bridge_client=bridge), bridge


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _observe(_: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    facade, bridge = _facade(metadata)
    try:
        _print_json({"live": metadata, "battlefield": facade.read_battlefield("all"),
                     "alerts": facade.get_alerts(), "missions": facade.read_missions(include_terminal=True)})
    finally:
        bridge.close()
    return 0


def _status(_: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    try:
        facade, bridge = _facade(metadata)
        try:
            state = bridge.get_state()
            metadata.update({"status": state.phase, "tick": int(state.tick), "player_faction": state.player_faction,
                             "enemy_faction": state.enemy_faction, "player_spawn": int(state.player_spawn), "enemy_spawn": int(state.enemy_spawn)})
        finally:
            bridge.close()
    except Exception as exc:
        metadata.update({"status": "unreachable", "error_message": str(exc)})
    _print_json(metadata)
    return 0


def _issue(args: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    facade, bridge = _facade(metadata)
    try:
        mission = json.loads(args.mission_json)
        result = facade.issue_mission(mission)
        _print_json(result)
        return 0 if result.get("ok") else 2
    finally:
        bridge.close()


def _target_from_text(text: str) -> str:
    lowered = text.lower()
    if any(token in text for token in ("北西", "north west", "northwest")):
        return "oil_north_west"
    if any(token in text for token in ("东北", "北东", "north east", "northeast")):
        return "oil_north_east"
    if any(token in text for token in ("东边", "东侧", "east")):
        return "oil_center_east"
    if any(token in text for token in ("西边", "西侧", "west")):
        return "oil_center_west"
    if "oil" in lowered or "油" in text:
        return "oil_center_west"
    return "oil_center_west"


def _mission_from_text(text: str) -> dict[str, Any]:
    if any(token in text.lower() for token in ("cancel", "取消")):
        raise ValueError("取消任务请使用 cancel <mission_id>")
    if any(token in text.lower() for token in ("capture", "oil", "抢", "占", "油")):
        return {"id": f"live_capture_{uuid.uuid4().hex[:8]}", "type": "capture", "priority": "high",
                "ttl_ticks": 1500, "target": _target_from_text(text), "unit_type": "e6", "escort_units": 1,
                "notes": f"human turn: {text[:160]}"}
    if any(token in text.lower() for token in ("produce", "train", "生产")):
        actor = "e1"
        match = re.search(r"\b([a-z][a-z0-9]{1,8})\b", text.lower())
        if match:
            actor = match.group(1)
        return {"id": f"live_produce_{uuid.uuid4().hex[:8]}", "type": "produce", "priority": "medium",
                "ttl_ticks": 1500, "actor_type": actor, "count": 1, "notes": f"human turn: {text[:160]}"}
    raise ValueError("当前 smoke-test 解析器只支持 capture/produce；正式 Agent 请使用 MCP issue_mission")


def _turn(args: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    facade, bridge = _facade(metadata)
    try:
        before = {"battlefield": facade.read_battlefield("all"), "alerts": facade.get_alerts(), "missions": facade.read_missions()}
        try:
            mission = _mission_from_text(args.text)
        except ValueError as exc:
            _print_json({"ok": False, "error": str(exc), "before": before})
            return 2
        issued = facade.issue_mission(mission)
        mission_id = issued.get("data", {}).get("mission_id")
        status = None
        deadline = time.monotonic() + args.wait_seconds
        while mission_id and time.monotonic() < deadline:
            status = facade.read_missions(include_terminal=True, mission_id=mission_id)
            rows = status.get("data", {}).get("missions", []) if status.get("ok") else []
            if rows and rows[0].get("status") in {"accepted", "blocked", "in_progress", "succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        _print_json({"ok": bool(issued.get("ok")), "human_text": args.text, "before": before,
                     "issued": issued, "status": status, "note": "下达证明来自 mission event + C# mission-status；执行证明来自状态与 audit 日志"})
        return 0 if issued.get("ok") else 2
    finally:
        bridge.close()


def _cancel(args: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    facade, bridge = _facade(metadata)
    try:
        result = facade.cancel_mission(args.mission_id, args.reason)
        _print_json(result)
        return 0 if result.get("ok") else 2
    finally:
        bridge.close()


def _stop(_: argparse.Namespace) -> int:
    if not METADATA_PATH.exists():
        print("没有活动的可见会话")
        return 0
    metadata = _read_json()
    runtime_root = _safe_runtime_root(Path(metadata.get("runtime_root", RUNTIME_ROOT)))
    (runtime_root / "live-stop").write_text("stop\n", encoding="utf-8")
    deadline = time.monotonic() + 10
    supervisor_pid = int(metadata.get("supervisor_pid", 0) or 0)
    while supervisor_pid and time.monotonic() < deadline:
        try:
            os.kill(supervisor_pid, 0)
        except (OSError, ProcessLookupError):
            break
        time.sleep(0.25)
    _print_json(_read_json())
    return 0


def _mcp(args: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    # The script is launched by file path, so Python's import path contains
    # ``commander/scripts`` rather than the repository root.  Use the same
    # repository-local import bootstrap as the other commands before starting
    # the formal Phase 1c MCP server.
    _imports()
    runtime_root = Path(metadata["runtime_root"])
    session_id = str(metadata["session_id"])
    os.environ.update({"RL_SESSION_ID": session_id, "RL_OBSERVATION_DIR": str(runtime_root),
                       "RL_MISSION_DIR": str(runtime_root), "RL_EVENT_DIR": str(runtime_root)})
    from openra_env.phase1c_mcp import main as mcp_main
    old_argv = sys.argv
    try:
        sys.argv = ["openra-rl-phase1c-mcp", "--transport", args.transport]
        mcp_main()
    finally:
        sys.argv = old_argv
    return 0


def _mcp_config(_: argparse.Namespace) -> int:
    metadata = _metadata_or_die()
    _print_json({"mcpServers": {"openra-commander-live": {
        "command": sys.executable,
        "args": [str(SCRIPT), "mcp", "--transport", "stdio"],
        "env": {"RL_SESSION_ID": metadata.get("session_id", "<start first>"),
                 "RL_OBSERVATION_DIR": metadata.get("runtime_root", str(RUNTIME_ROOT)),
                 "RL_MISSION_DIR": metadata.get("runtime_root", str(RUNTIME_ROOT)),
                 "RL_EVENT_DIR": metadata.get("runtime_root", str(RUNTIME_ROOT))},
    }}})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start and operate a visible Phase 1d OpenRA game")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start a visible game and keep the bridge connected")
    start.add_argument("--openra-path", default=os.environ.get("OPENRA_PATH", str(PROJECT_ROOT / "OpenRA")))
    start.add_argument("--map", default="agenda.oramap")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=9999)
    start.add_argument("--runtime-root", default=str(RUNTIME_ROOT))
    start.add_argument("--log-path", default=str(LOG_PATH))
    start.add_argument("--ready-retries", type=int, default=240)
    start.add_argument("--headless", action="store_true", help="CI fallback; default is a visible desktop window")
    start.add_argument("--foreground", action="store_true", help="keep the supervisor in this terminal")
    supervisor = sub.add_parser("supervisor", help=argparse.SUPPRESS)
    for name, default in (("openra-path", os.environ.get("OPENRA_PATH", str(PROJECT_ROOT / "OpenRA"))), ("map", "agenda.oramap"),
                          ("host", "127.0.0.1"), ("runtime-root", str(RUNTIME_ROOT)), ("log-path", str(LOG_PATH))):
        supervisor.add_argument(f"--{name}", default=default)
    supervisor.add_argument("--port", type=int, default=9999)
    supervisor.add_argument("--ready-retries", type=int, default=240)
    supervisor.add_argument("--headless", action="store_true")
    sub.add_parser("status")
    sub.add_parser("observe")
    turn = sub.add_parser("turn", help="human smoke-test convenience; formal Agent uses MCP")
    turn.add_argument("text")
    turn.add_argument("--wait-seconds", type=float, default=3)
    issue = sub.add_parser("issue")
    issue.add_argument("--mission-json", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("mission_id")
    cancel.add_argument("--reason", default="operator_cancel")
    mcp = sub.add_parser("mcp", help="run the five-tool Phase 1c MCP server for the live session")
    mcp.add_argument("--transport", choices=("stdio", "sse"), default="stdio")
    sub.add_parser("mcp-config", help="print client configuration for this live session")
    sub.add_parser("stop")
    args = parser.parse_args()
    if args.command == "start":
        if METADATA_PATH.exists():
            try:
                current = _read_json()
                if current.get("status") in {"starting", "playing"} and _pid_alive(int(current.get("supervisor_pid", 0) or 0)):
                    print(json.dumps(current, ensure_ascii=False, indent=2))
                    return 0
            except Exception:
                pass
        if args.foreground:
            supervisor_args = argparse.Namespace(
                openra_path=args.openra_path, map=args.map, host=args.host, port=args.port,
                runtime_root=args.runtime_root, log_path=args.log_path, ready_retries=args.ready_retries,
                headless=args.headless)
            return _supervisor(supervisor_args)
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        log = Path(args.log_path).open("a", encoding="utf-8")
        venv_python = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        runtime_python = str(venv_python) if venv_python.exists() else sys.executable
        # Replace a stale error/finished metadata file before waiting for the
        # new supervisor; otherwise the wait loop could immediately return the
        # previous session's status.
        _write_json(METADATA_PATH, {"schema_version": 1, "profile": "phase1d-live-visible", "status": "starting",
                                    "runtime_root": str(Path(args.runtime_root).resolve()), "runtime_python": runtime_python,
                                    "requested_at_unix_ms": int(time.time() * 1000)})
        child_args = [runtime_python, str(SCRIPT), "supervisor", "--openra-path", args.openra_path, "--map", args.map,
                      "--host", args.host, "--port", str(args.port), "--runtime-root", args.runtime_root, "--log-path", args.log_path,
                      "--ready-retries", str(args.ready_retries)]
        if args.headless:
            child_args.append("--headless")
        kwargs: dict[str, Any] = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
                                 "close_fds": True}
        if os.name == "nt":
            # A redirected log handle keeps the supervisor independent of the
            # invoking shell.  DETACHED_PROCESS breaks Python/venv startup on
            # some managed Windows hosts, so use a new process group only.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        try:
            process = subprocess.Popen(child_args, **kwargs)
        except PermissionError:
            # Managed sandboxes may deny CREATE_BREAKAWAY_FROM_JOB.  Retry
            # with the portable process-group flag; an interactive terminal
            # does not normally need the breakaway bit.
            if os.name != "nt":
                raise
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(child_args, **kwargs)
        log.close()
        # Keep the exact interpreter visible in metadata; this is useful when
        # a Windows machine has a system Python without grpcio installed.
        try:
            if METADATA_PATH.exists():
                current = _read_json()
                current["runtime_python"] = runtime_python
                current["supervisor_pid"] = process.pid
                _write_json(METADATA_PATH, current)
        except Exception:
            pass
        deadline = time.monotonic() + max(10, args.ready_retries)
        while time.monotonic() < deadline:
            if METADATA_PATH.exists():
                current = _read_json()
                if current.get("session_id") or current.get("status") == "error":
                    _print_json(current)
                    return 0 if current.get("session_id") else 2
            if process.poll() is not None:
                break
            time.sleep(0.25)
        _print_json(_read_json() if METADATA_PATH.exists() else {"status": "starting", "supervisor_pid": process.pid})
        return 0
    if args.command == "supervisor":
        return _supervisor(args)
    if args.command == "status":
        return _status(args)
    if args.command == "observe":
        return _observe(args)
    if args.command == "turn":
        return _turn(args)
    if args.command == "issue":
        return _issue(args)
    if args.command == "cancel":
        return _cancel(args)
    if args.command == "mcp":
        return _mcp(args)
    if args.command == "mcp-config":
        return _mcp_config(args)
    if args.command == "stop":
        return _stop(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
