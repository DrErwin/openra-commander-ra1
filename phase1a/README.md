# Phase 1a 扩展区

本目录只放本项目新增的 Phase 1a 测试、运行证据和辅助文件。原有 OpenRA-RL Python 工程仍位于仓库根目录，原始 OpenRA 源码仍位于 `OpenRA/`。

对照基线位于 `D:\Agent RA2\work\OpenRA-RL`，仅用于比较和回归，不属于本实现 clone；本轮已核对该目录工作树保持洁净。

## 目录约定

- `scripts/`：Phase 1a 回归和 baseline 测试入口。
- `tests/`：Phase 1a 静态契约测试。
- `evidence/`：本地运行日志和临时证据；正式 JSON 证据位于项目文档目录 `D:\Agent RA2\document/evidence/phase1a/`。
- `runtime/`：运行时 fallback 文件目录，不作为源码提交。
- `OpenRA/OpenRA.Mods.Common/Traits/Player/Phase1a/`：编译所需的新增 C# trait、session 和文件存储实现。

对已有基线文件的修改不复制到这里，以避免产生两份会漂移的源码；这些修改在 `document/phase-file-change-log.md` 中逐项记录。
