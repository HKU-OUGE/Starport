# 新 Mac 的准备清单

Mac 作为 B：保存项目、Claude 和 Starship。A 只调用受限接口。

## 必需软件

| 项目 | 操作 |
| --- | --- |
| Python | 安装 [Python 3](https://www.python.org/downloads/macos/)，终端检查 `python3 --version`，要求 3.10+ |
| Claude Code | 按[官方说明](https://code.claude.com/docs/en/setup)安装，在 Mac 本机登录 |
| 开发工具 | 项目需要 Git／编译器时运行 `xcode-select --install`，完成系统安装窗口 |
| Starship | 仓库内运行 `python3 tools/preflight.py client`，再运行 `python3 setup.py client` |

Mac 仅作客户端时不需要 Node.js。作为 A 时才需要服务端的 Node.js 和 npm。

## 远程登录

在 **系统设置 → 通用 → 共享 → 远程登录** 中启用 SSH，只允许准备运行 Starship 的用户。
记录用户短名称和预定私有接口的地址，然后按[配对步骤](PAIRING.md)授权专用公钥。
[Apple 远程登录说明](https://support.apple.com/guide/mac-help/mchlp1066/mac)。

| 权限／服务 | 基本会话控制是否必需 |
| --- | --- |
| 远程登录 SSH | 是，A 通过它调用 Starship |
| Claude 本机账号登录 | 是，与 Starport 网页访问码不同 |
| Full Disk Access | 默认不要求；只有实际需要的文件读取被系统拒绝时再核对 |
| 辅助功能、屏幕录制 | 仅桌面点击／屏幕串流等可选功能需要 |
| 文件共享 SMB、屏幕共享 VNC | 可选，网页附件与 Remote Control 不依赖它们 |

## 登录后启动与锁屏

正常安装会注册当前用户的四个 launchd 服务：会话、活动状态、模型设置、账号检查。
安装器记录 Claude 的启动位置，并检查用户原生安装目录；后台不依赖终端中的 PATH。

```sh
python3 starship.py status
launchctl print gui/$(id -u)/io.starport.starship
```

锁屏后可以继续使用已运行的后台会话。关机、睡眠、退出登录和 FileVault 开机解锁不同，
需要分别实测；不要用“关闭屏幕”代替防止整机睡眠。服务随用户登录恢复，不承诺无人登录就可用。

手动运行：安装时增加 `--no-register`，再执行 `python3 tools/run_services.py client`。
关闭该终端会停止手动服务，先结束其管理的工作。

## 文件与会话

项目放在 Mac 用户目录里的普通文件夹。先通过网页上传一张小图片，确认能发送、预览和下载。
不需要为这一步开启整个磁盘的 SMB 共享。原生 Desktop 会话要打开 Remote Control；
独立后台会话可以在锁屏时工作，但不会自动成为 Desktop 同一条历史。

| 现象 | 检查顺序 |
| --- | --- |
| 终端可用，后台提示找不到 Claude | `tools/preflight.py client` → 私有 `runner.json` → Claude 是否被移动 |
| 服务安装失败 | `python3 setup.py client --check`；旧服务有活动会话时先等待 |
| 文件不可读 | 文件是否属于该会话、是否移动、是否有符号链接、系统权限是否拒绝 |
| 锁屏后掉线 | 区分锁屏与睡眠；检查 SSH、Starship 和 Remote Control 三层 |
