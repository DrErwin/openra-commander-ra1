# OpenRACommander 工作区（`commander/`）

本目录存放 OpenRACommander 项目自己的 Python 工具与运行产物。上游 OpenRA-RL 的 Python 工程仍在仓库根目录 `openra_env/`，引擎源码在 `OpenRA/`；本目录**不复制**上游代码，只放我们新增的脚本、测试和运行时文件，并随项目阶段累积。

新增的 C# 扩展在 `OpenRA/OpenRA.Mods.Common/Traits/Player/Commander/`（Steering / Observation / session / file store）。

## 目录约定

- `scripts/`：回归与基线测试入口。当前为 `regression.py`（5-seed canonical 回归）与 `baseline.py`（normal vs agent-normal tick 速率对比）。
- `tests/`：静态契约测试。当前为 `test_contract.py`（proto 镜像、observation envelope schema、agent-normal 模块隔离、Steering 无下单接口、Python 语法）。
- `evidence/`：本地运行日志与临时证据（gitignored，不入库）。
- `runtime/`：运行时 fallback 文件目录（gitignored）。

正式 JSON 证据按**阶段**归档到 `D:\Agent RA2\document\evidence\phase1a\`（phase1a 阶段产物），不放在本目录——证据是一次性归档产物，按阶段命名合理。

## 边界

- 对上游基线文件的**修改**不复制到本目录（避免两份会漂移的源码），逐项记录在 `D:\Agent RA2\document\phase-file-change-log.md`。
- 对照基线（干净 upstream）在 `D:\Agent RA2\work\OpenRA-RL`，仅用于对比回归，不属于本实现 clone。
