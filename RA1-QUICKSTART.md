# RA1 AI 指挥版：克隆后开始游玩

本仓库发布的是已经完成验收的 RA1 版本。默认会打开一个可见的《红色警戒》游戏窗口，`agent-normal` 自主经营和作战；外部 AI 通过 MCP 读取战场、发布任务并查询执行结果。

## 准备条件

- Windows 10/11
- Git for Windows
- Python 3.10 或更高版本（安装 `uv` 可加快依赖安装，但不是必需）
- .NET 8 SDK

## 1. 克隆和安装

```powershell
git clone --recurse-submodules https://github.com/DrErwin/openra-commander-ra1.git
cd openra-commander-ra1
powershell -ExecutionPolicy Bypass -File .\setup-ra1.ps1
```

安装脚本会自动取得本仓库发布的 RA1 引擎版本、创建 `.venv`、安装 Python 控制端，并编译 `OpenRA.exe`。

## 2. 启动可见游戏

```powershell
powershell -ExecutionPolicy Bypass -File .\play-ra1.ps1
```

脚本会启动 RA1 地图 `Agenda`，玩家侧使用 `agent-normal`，敌方使用 `easy`。终端随后会输出当前会话专用的 MCP 配置。

无桌面的测试机器可以运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\play-ra1.ps1 -Headless
```

## 3. 让 AI 指挥兵种

把 `play-ra1.ps1` 输出的 MCP 配置加入支持 MCP 的 AI 客户端，例如 Codex 或 Claude。连接后，AI 会获得五个面向战术的工具：

- `read_battlefield`：读取战场态势。
- `get_alerts`：读取重要告警。
- `read_missions`：查看任务执行状态。
- `issue_mission`：下达进攻、占领或生产任务。
- `cancel_mission`：撤销任务。

可以直接对 AI 说：“先读取战场，然后组织部队占领中间偏西的油井；兵力不足时先生产，再继续执行。”

这里的 AI 不直接操作每个单位编号。它下达可读的战术任务，引擎内的 RA1 Bot 负责采矿、建造、生产、编队和具体移动，因此指挥节奏更接近真正的指挥官。

## 常用检查和停止命令

```powershell
.\.venv\Scripts\python.exe commander\scripts\live_operator.py status
.\.venv\Scripts\python.exe commander\scripts\live_operator.py observe
powershell -ExecutionPolicy Bypass -File .\stop-ra1.ps1
```

如果启动失败，查看 `commander\evidence\live-operator.log`。首次安装需要从 NuGet 和 PyPI 下载依赖，之后可离线重复启动。
