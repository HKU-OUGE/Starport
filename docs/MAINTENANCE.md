# 排障、更新与卸载

## 先定位哪一层

```text
浏览器 → A 的网页服务 → SSH → B 的 Starship → Claude 登录／Remote Control
```

| 问题 | 第一条检查 |
| --- | --- |
| 页面打不开 | A：`python3 starport.py urls`、`python3 starport.py services` |
| 设备连不上 | A：`python3 tools/pair.py verify` |
| 客户端不工作 | B：`python3 starship.py status` |
| 安装缺依赖 | 对应设备：`python3 tools/preflight.py server` 或 `client` |
| Desktop 会话只读／消失 | B：原会话 Remote Control 是否开启、账号是否可用 |
| 节点显示重连 | 看当前连接状态与心跳；重试次数是历史计数 |
| 网络切换后无法访问 | 受控上联、路由器、代理与 DNS 分别检查 |

## 更新

1. 检查 `git status`，保留自己的修改；记录安装参数和外部配置。
2. 等受影响角色的活动会话结束，再停止该角色的服务。
3. `git pull --ff-only`，运行 `--check`，按原参数重新安装。
4. 核对 SSH、账号、一次消息和附件；保留原登录启动选择。

`--no-start` 会注册登录启动，只是不立即启动。`--no-register` 不建立新注册，
也不会移除已有注册。已有自启需要用下面的对应命令取消。

## 停止与取消登录启动

只操作要卸载的角色。先结束它管理的工作；停客户端会影响该客户端的会话。

| 角色 | 固定服务名称 |
| --- | --- |
| A | `starport-console`、`starport-panel` |
| B | `starship`、`starship-activity`、`starship-model-settings`、`starship-auth` |

Linux（以下是 A；B 使用上表名称并加 `.service`）：

```sh
systemctl --user disable --now starport-console.service starport-panel.service
```

macOS（逐个使用上表名称，前缀为 `io.starport.`）：

```sh
launchctl bootout gui/$(id -u)/io.starport.starport-console
launchctl bootout gui/$(id -u)/io.starport.starport-panel
```

随后移走对应的 `~/Library/LaunchAgents/io.starport.NAME.plist`，避免下次登录再加载。
Linux 可移走对应用户 unit 后运行 `systemctl --user daemon-reload`。

Windows PowerShell（每个任务分别执行）：

```powershell
schtasks.exe /End /TN 'Starport\starport-console'
schtasks.exe /Delete /TN 'Starport\starport-console' /F
```

手动运行的服务在原终端按 Ctrl+C 结束。不要停止路由虚拟机或训练服务来卸载会话工作台。

## 文件保留

默认保留配置、凭据、会话和项目。停止服务后可以移除已确认不用的程序副本。

| 内容 | 位置 |
| --- | --- |
| Starport 配置 | POSIX：`~/.config/starport`；Windows：`%APPDATA%\starport` |
| Starport 状态 | POSIX：`~/.local/state/starport`；Windows：`%LOCALAPPDATA%\starport` |
| Starship 程序与状态 | 用户目录下 `.local/share/starship` |
| SSH 配对 | A 的专用私钥／known-hosts；B 的对应公钥条目 |
| 用户项目 | 保持原位置，不随卸载删除 |

清理磁盘前列出具体目录、占用和保留副本，再决定删除。不要按“超过 24 小时”清空所有状态。
