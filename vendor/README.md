# 项目内运行时

`openra-official/` 是用于启动原版 Red Alert 的项目本地运行时目录。它不从任何外部参考 checkout 读取文件；`commander/scripts/start_official_openra.ps1` 只解析本目录。

目录中的二进制文件属于本机生成/安装产物，不纳入 Git。若在新机器上重新准备运行时，请将一个与项目兼容的官方 OpenRA Release 构建放到 `vendor/openra-official/`，至少包含 `bin/`、`mods/`、`glsl/`、`VERSION` 和 `AUTHORS`。
