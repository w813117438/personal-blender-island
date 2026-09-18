# 个人 GitHub 构建

个人开发分支使用 `personal`，推送代码会触发 `Personal Blender - Windows` 工作流。也可以在 GitHub 仓库的 Actions 页选择这个工作流并点击 Run workflow 手动构建。

在该次运行的 Artifacts 区下载 `blender-windows-portable-运行编号`（ZIP 便携包）或 `blender-windows-installer-运行编号`（MSI 安装包），每个包附带 SHA256 校验文件。ZIP 先打包并上传，所以之后 MSI 失败仍然能下载便携包。构建日志保留为独立附件，包含 CPack 和 WiX 的详细日志。附件保留 7 天。

MSI 使用磁盘根目录下新建的短临时路径打包，避免 WiX 3 无法读取超过 Windows MAX_PATH 限制的 Python 资源路径。临时目录由 GitHub 一次性执行器在作业结束后回收。

这是个人开发版，不是 Blender 官方发行版。安装包未做代码签名。源码来自 Blender，保留上游许可证及版权声明。

构建在 GitHub 的 Windows 2022 执行器上完成，包含源码资源下载、依赖准备、full 编译、后台启动检查、打包。首次云端构建可能需要较长时间；本机下载缓存不会自动上传到云端。工作流只使用读取仓库权限，不需要额外配置个人访问令牌。

源码资源从 Blender 官方 LFS 端点下载；`.local-build`、个人偏好设置及本机构建产物不会提交到仓库。CI 当前不缓存巨大的依赖目录，每次运行均在独立机器准备依赖。
