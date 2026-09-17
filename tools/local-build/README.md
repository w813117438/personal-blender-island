# 一键编译并启动 Blender

日常使用请双击根目录 `Open-Blender.cmd`：直接打开已经编译好的 Blender，不检查网络、不下载、不编译，并复用保存中文等偏好设置的配置目录。修改源码后需要测试新代码时，再运行 `Build-And-Run.cmd`。

双击源码根目录的 `Build-And-Run.cmd`。

程序使用官方 `make.bat full` 编译当前本地源码，成功后先做后台启动检查，再打开新编译的 Blender。首次自动下载便携式 CMake 和 Windows 预编译依赖库，需要联网、较多磁盘空间和较长时间；以后重复运行会增量编译。不会更新或覆盖你的源码修改。

需要 VS 2022 17.14.14 或更新的 2022 版本，C++ 工具版本至少 14.44.35216，以及 Windows SDK、Git / Git LFS。VS 2019 不满足当前源码要求。缺少时双击根目录 `Install-Build-Tools.cmd` 安装 2022 C++ Build Tools，完成后重试。安装器可能要求管理员权限或重启。为匹配便携 CMake，启动脚本当前固定选择 VS 2022。

从 VS 2019 切换到 VS 2022 时，脚本自动备份不兼容的旧构建目录并重新配置，下载过的源码资源和依赖库继续复用。

版本检查读取实际 `cl.exe` 文件版本（至少 19.44.35216），不使用工具目录名称判断；微软补丁更新后，目录名称可能仍保留旧版本号。

源码保持使用 GitHub 镜像；LFS 大文件从 Blender 官方 projects.blender.org 下载，因为 GitHub 镜像不提供这些资源。

在源码目录终端中可运行：

```bat
Build-And-Run.cmd -Action Check
Build-And-Run.cmd -Action Prepare
Build-And-Run.cmd -Action Run
Build-And-Run.cmd -NoLaunch
Build-And-Run.cmd -Profile lite
```

- Check：检查环境，不下载或编译。
- Prepare：仅下载构建工具和依赖。
- Run：打开已有 full 构建；lite 构建加 `-Profile lite`。
- NoLaunch：编译并后台检查，不打开窗口。
- lite：精简功能编译，不含 Cycles 等功能；默认 full 保留更多功能，但不预编译 CUDA 内核。

构建产物位于 `.local-build/build-full/bin/Release/blender.exe`（lite 对应 build-lite），日志位于 `.local-build/logs`。首次构建失败后查看最后一条错误，解决后重新运行即可。

正常打开的窗口会读取独立配置目录 `.local-build/user-config` 中保存的语言、偏好设置和启动场景。修改中文后请确认偏好设置已保存，之后通过此脚本启动会保留设置。仅后台启动检查使用工厂设置，不会保存或重置你的偏好设置。程序只会在构建及后台检查成功后自动启动；失败时不会打开旧产物。这里的后台检查不是完整自动化测试套件。
