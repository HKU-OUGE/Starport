# 安装与更新

## 选择角色

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Starport 服务端 | systemd 用户服务 | launchd 用户代理 | 用户登录任务 |
| Starship 客户端 | Claude Code | Claude Code / Desktop | Claude Code / Desktop |
| 本地通信 | 带认证的回环 JSON | 带认证的回环 JSON | 带认证的回环 JSON |
| 软路由 | 现有双守卫 / Linux 网关 | OpenWrt / Linux 虚拟机 | OpenWrt / Linux 虚拟机 |

两端需要 Python 3.10+。服务端还需要 Node.js 22.12+、npm、OpenSSH，以及 Python venv 或 uv。客户端先安装并登录 Claude Code。

## Starport

Linux / macOS：

```sh
python3 setup.py server --check
python3 setup.py server --ssh-host starship
python3 starport.py urls
python3 starport.py key
```

Windows PowerShell：

```powershell
py -3 setup.py server --check
py -3 setup.py server --ssh-host starship
py -3 starport.py urls
py -3 starport.py key
```

Starship 运行在 Windows 时，给服务端安装命令增加 `--client-platform windows`。Linux／macOS 客户端使用默认格式。

默认会话端口 18881、管理端口 18882，仅监听回环。`--host` 指定 VPN 私有地址，`--allowed-subnet` 指定管理面板的私有访问网段。`--management-url` 设置会话控制台中的管理链接。

HTTPS 可使用已有 Access + Tunnel：源站连接回环端口 18883，`--public-origin` 设置精确 HTTPS 域名。入口必须验证身份，控制台继续要求访问码和 CSRF。

## Starship

```sh
python3 setup.py client --check
python3 setup.py client
python3 starship.py status
```

Windows 将 `python3` 换成 `py -3`，也可运行 `./install.ps1 client`。客户端代码保存在用户目录的 `.local/share/starship`；本地通信只绑定回环，令牌保存在私有目录。Windows 目录使用本用户 ACL，POSIX 使用 0700 / 0600 权限。

启用设备的 OpenSSH 服务，核对主机指纹后按 [SSH 示例](../examples/ssh.example.conf) 配对。专用密钥建议配置固定命令：POSIX 使用 `.local/share/starship/rpc`，Windows 使用 `.local\share\starship\rpc.cmd`。服务端不会转发 SSH agent、环境变量或浏览器地址。

原生会话需开启 Remote Control。独立后台会话不依赖桌面窗口；服务默认随用户登录启动，锁屏继续运行。Linux 可额外启用 linger。重启后未登录用户会话时，macOS／Windows 后台任务不会自动获得原生登录会话。

## 软路由与已有服务

```sh
python3 setup.py server --router-profile ../private/router-remote.json
```

三系统使用 [远端网关配置](../examples/router-remote.example.json) 管理已有 Linux 网关；Linux 原部署可继续用 [本机适配配置](../examples/router-profile.example.json)。部署与网口边界见[网络与出口](NETWORK.md)。

## 更新与验收

`--plan` 仅生成计划；可增加 `--platform Linux`、`Darwin` 或 `Windows` 查看对应服务定义。`--no-start` 只写入配置和注册文件。Starship 更新会拒绝覆盖仍有活动锁或未结束后台会话的安装。

自动检查覆盖三系统的核心通信、文件读写、附件、服务定义和网页构建；实际账号登录、开机后登录恢复、专用网口及代理全故障仍需在部署设备上验收。Linux 的原软路由事务测试保留。

[Claude Code 官方平台要求](https://code.claude.com/docs/en/setup)包括 Linux、macOS 和 Windows；Starship 在 Linux 上使用 Claude Code。
