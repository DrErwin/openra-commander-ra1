# Phase 1d Demo

这是一个只读的本地 dashboard。`phase1d_demo.py` 从 `document/evidence/phase1c/` 读取已提交的 canonical MCP/replay 证据，生成 `phase1d-demo-state.json`、`missions.yaml` 和 replay/audit timeline，再提供两个 GET 路由：`/` 与 `/api/state`。

该 dashboard 仍然是回放/状态展示面；要在可见游戏里实际让 Agent 读态势、发任务，请使用上层监督器：

```powershell
.\.venv\Scripts\python.exe commander\scripts\live_operator.py start
.\.venv\Scripts\python.exe commander\scripts\live_operator.py observe
.\.venv\Scripts\python.exe commander\scripts\live_operator.py mcp-config
```

监督器启动 `OpenRA/bin/OpenRA.exe`，保持 `GameSession` 心跳，MCP 仍只暴露语义工具；任务写入由 `mission-commands.jsonl` 记录，执行状态由 C# 独占写入 `mission-status.json` 和 `mission-audit.jsonl`。

```powershell
uv run --with grpcio --with protobuf --with pydantic --with mcp `
  python commander/scripts/phase1d_rehearsal.py
uv run --with grpcio --with protobuf --with pydantic --with mcp `
  python commander/scripts/phase1d_demo.py serve --port 8091
```

无 Docker/noVNC 时，战场区域使用 replay/state fallback；有 noVNC 时追加 `--novnc-url`。本页面没有任务写入 API，不替代 MCP 控制面。
