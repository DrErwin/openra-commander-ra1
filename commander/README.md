# OpenRACommander 工作区（`commander/`）

本目录存放 OpenRACommander 项目自己的 Python 工具与运行产物。上游 OpenRA-RL 的 Python 工程仍在仓库根目录 `openra_env/`，引擎源码在 `OpenRA/`；本目录**不复制**上游代码，只放我们新增的脚本、测试和运行时文件，并随项目阶段累积。

新增的 C# 扩展在 `OpenRA/OpenRA.Mods.Common/Traits/Player/Commander/`（Steering / Observation / session / file store / Mission Control）；mission request 实现在 `BotModules/`。

## 目录约定

- `scripts/`：回归与基线测试入口。除 1a 脚本外，`mission_gate.py` 运行 canonical 180 秒 attack→capture→cancel 闭环，`mission_regression.py` 运行 5 个固定 seed；设置 `RL_RECORD_REPLAYS=true` 时仍按 session 独立写 replay。自然整局 replay 是后续增强证据，不阻塞 bounded 1a/1b 验收。
- `tests/`：静态契约测试。`test_contract.py` 覆盖 schema/YAML/Steering，`test_mission_control.py` 覆盖 mission JSONL、session、torn tail、lease/save-load surface。
- 1c 语义面：`openra_env/phase1c.py` 和 `openra_env/phase1c_mcp.py` 只对 Agent 暴露 `read_battlefield`、`get_alerts`、`read_missions`、`issue_mission`、`cancel_mission` 五个工具；`commander/scripts/phase1c_gate.py` 默认运行离线契约 fixture，`--live` 用于连接真实 daemon。
- 1d 发布面：`commander/scripts/phase1d_demo.py` 生成只读 dashboard，`phase1d_rehearsal.py` 做冷启动 HTTP rehearsal，`phase1d_gate.py` 汇总 canonical/5-seed 证据。默认没有 noVNC 时使用 replay/state fallback；追加 `--novnc-url` 才嵌入外部 noVNC 画面。
- 1d 页面只读取 `/api/state`，不发 mission；任务仍由 MCP 五工具和 C# engine-owned 文件协议处理。
- `evidence/`：本地运行日志与临时证据（gitignored，不入库）。
- `runtime/`：运行时 fallback 文件目录（gitignored）。

正式 JSON 证据按**阶段**归档到 `D:\Agent RA2\document\evidence\phase1a\`、`D:\Agent RA2\document\evidence\phase1b\`，不放在本目录——证据是一次性归档产物，按阶段命名合理。

## 边界

- 对上游基线文件的**修改**不复制到本目录（避免两份会漂移的源码），逐项记录在 `D:\Agent RA2\document\phase-file-change-log.md`。
- 对照基线（干净 upstream）在 `D:\Agent RA2\work\OpenRA-RL`，仅用于对比回归，不属于本实现 clone。
