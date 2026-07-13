# Phase 1d Demo

这是一个只读的本地 dashboard。`phase1d_demo.py` 从 `document/evidence/phase1c/` 读取已提交的 canonical MCP/replay 证据，生成 `phase1d-demo-state.json`、`missions.yaml` 和 replay/audit timeline，再提供两个 GET 路由：`/` 与 `/api/state`。

```powershell
uv run --with grpcio --with protobuf --with pydantic --with mcp `
  python commander/scripts/phase1d_rehearsal.py
uv run --with grpcio --with protobuf --with pydantic --with mcp `
  python commander/scripts/phase1d_demo.py serve --port 8091
```

无 Docker/noVNC 时，战场区域使用 replay/state fallback；有 noVNC 时追加 `--novnc-url`。本页面没有任务写入 API，不替代 MCP 控制面。
