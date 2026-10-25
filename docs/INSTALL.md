# 安装与更新

## Starport · Linux

```sh
./install.sh server --check
./install.sh server --ssh-host starship
./bin/starport urls
./bin/starport key
```

安装器建立 `server/.venv`，构建控制台网页，注册 `starport-console.service` 和 `starport-panel.service`。默认端口分别为 18881、18882，均仅监听回环。`--plan` 只输出注册计划；`--no-start` 写配置和注册，不重启现有进程。

配置位于 XDG 配置目录的 `starport/`，状态位于 XDG 状态目录的 `starport/`。运行目录权限 0700，访问码和本地配置权限 0600。生成的系统服务使用安装机上的实际路径。

要从 VPN 访问，在命令中加 `--host` 指定已有私有地址，管理面板加 `--allowed-subnet` 指定私有网段。控制台的“工具管理”链接由 `--management-url` 配置，留空隐藏。不要让隔离设备访问管理面板。

HTTPS 可使用已有 Cloudflare Access + Tunnel，将源站指向回环端口 18883，并用 `--public-origin https://console.example.invalid` 配置精确域名。必须在 Tunnel/Access 侧验证访问身份；控制台继续要求访问码和 CSRF。安装器不创建域名、账户或隧道。

## Starship · macOS

先安装 Python 3.10+，完成原生 Claude Code 登录，并开启系统“远程登录”。

```sh
./install.sh client --check
./install.sh client
./bin/starship status
```

Starship 安装到用户目录中的 `.local/share/starship/`，注册四个 `io.starport.starship*` LaunchAgent。Desktop 已开启 Remote Control 的会话自动出现；独立后台会话不要求解锁桌面。重启后仍需首次登录 macOS。

服务端按 [SSH 示例](../examples/ssh.example.conf) 建立 `starship` 别名，使用专用密钥，核对主机指纹后保存 known_hosts。建议给设备端公钥加示例中的 `restrict` 和固定命令；该密钥只能访问会话、状态、成品与文件读取接口，不能开启任意 shell。

```sh
ssh starship '"$HOME/.local/share/starship/rpc" session' <<'JSON'
{"action":"health"}
JSON
```

## 现有路由与算力

```sh
./install.sh server --legacy-root ../existing-tools --router-profile ../private/router-profile.json
```

适配器复用已有服务、软路由守卫、订阅及训练监控。没有适配器时显示未接入，不自动部署网络、申请算力或安装模型。软路由适配要求见[网络指南](NETWORK.md)。

原小型管理面板安装方式 `./install.sh --legacy-root ../existing-tools` 仍可使用；旧配置目录和服务名保持兼容。迁移到新名称应在空闲时停用旧面板，再注册新面板，避免同端口重复运行。

## 更新

Starport 更新只重启控制台与面板，不重启训练、路由或 Starship。发送中的请求应先核对结果，再更新。配置与 unit 会保留 `.previous` 副本。

Starship 更新会拒绝覆盖运行中的后台服务。先结束其后台工作，使用 `launchctl bootout` 卸载对应 LaunchAgent，再重新安装；不删除会话记录。首次安装验收应检查 SSH、锁屏操作、媒体下载，以及实际设备的出口断线保护。
