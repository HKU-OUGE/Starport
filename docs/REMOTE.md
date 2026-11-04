# 发布远程域名

```text
手机 → HTTPS → Access 邮箱验证 → Tunnel → A 的专用入口 → 会话控制台 → B
```

先完成[本地会话验收](QUICKSTART.md)。在 A 的管理面板打开 **远程域名**，即可配置发布。
B 不需要公开端口，也不会收到 Cloudflare 凭证。

## 1. 准备

- 一个已接入 Cloudflare 的域名。
- 已开通 Zero Trust 团队，并配置登录方式，例如邮箱验证码。
- 允许使用这个控制台的邮箱。
- 在 A 安装 `cloudflared`，更新服务端依赖。

```sh
python3 -m pip install -r server/requirements.txt
```

在运行 Starport 的 Python 环境执行；Windows 使用 `py -3`。
安装器创建的环境位于 `server/.venv`，更新时使用其中的 Python。

| A 的系统 | cloudflared 安装方式 |
| --- | --- |
| Windows | `winget install --id Cloudflare.cloudflared --exact` |
| macOS（已有 Homebrew） | `brew install cloudflared` |
| Linux | 按 [Cloudflare 官方软件源](https://pkg.cloudflare.com/)安装 |

要求 `cloudflared` 2025.4.0 或更新版本，以支持 `--token-file`。安装后重开终端或重启管理面板，
再查看依赖状态。自定义位置可在面板配置中指定 `cloudflared_binary`，网页不能输入任意执行命令。

## 2. 创建 API Token

Token 只用于指定账户和域名。Cloudflare 界面可能把 Edit 显示为 Write。

| 范围 | 权限 |
| --- | --- |
| 指定账户 | Cloudflare One Connector: cloudflared — Write |
| 指定账户 | Access: Apps and Policies — Write |
| 指定账户 | Access: Organizations, Identity Providers, and Groups — Read |
| 指定域名 | Zone — Read；DNS — Write |

不需要 Global API Key、Workers 或 R2 权限。Token 粘贴到 A 的管理面板，不发进 Claude 会话。

## 3. 预览并发布

1. 填写域名、前缀、允许登录的邮箱和 API Token。
2. 点 **检查并预览**：检查权限，选择空闲域名；这一步不创建云端资源。
3. 核对实际域名和邮箱。前缀已占用时，会增加随机后缀，不覆盖旧记录。
4. 按需勾选 **随 Starport 管理面板自动恢复入口**，点 **发布并启动**。
5. 打开返回的 HTTPS 地址，完成邮箱验证，再输入原控制台访问码。

系统按顺序创建可复用的邮箱策略、Access 应用、Tunnel 和 DNS。源站再次验证 Access JWT 的
签名、签发方、受众、有效期和邮箱；仅通过验证的请求进入控制台。上传、Range 视频读取和文件下载
通过流式转发，保留原访问码、CSRF 和会话权限。

专用入口默认只监听 A 的 `127.0.0.1:18884`，转发目标来自本机已配置的会话地址。
不需要修改旧控制台的 `public_origin` 或重启会话服务。端口冲突时，可在面板配置中设置
`publication_port`。原有手工配置的 18883 入口保持独立。

## 4. 看状态与恢复

| 状态／操作 | 含义 |
| --- | --- |
| 本机连接运行中 | cloudflared 进程存在，不等于外网已可用 |
| 核对云端状态 | 重新核验 Access、Tunnel 和 DNS，并显示云端连接情况 |
| 登录验收 | 收到通过完整 JWT 验证的远程请求后记录时间 |
| 停止入口 | 只停止本次连接；本地会话和训练继续运行 |
| 更换 API Token | 保留当前发布记录，更新管理凭证 |
| 撤回本次发布 | 核对归属后删除本次 DNS、Tunnel、Access 应用和策略 |

中断或超时后先点 **核对云端状态**，确认后继续。创建结果不确定时不会盲目重试；
未能确认的资源需要管理员核对。撤回遇到已被其他人修改的资源会停止，不删除其他入口。
勾选自动恢复后，连接随已有管理面板服务恢复；没有注册面板自启时，需要手动启动面板。

## 已有 VPN 或多人使用

已有私有 VPN 时仍可直接访问 A 的私有地址，无需发布域名。
不同使用者的独立控制台应分别配置状态目录、端口、域名和 Access 应用。
**同一控制台的邮箱白名单不等于按会话分权。**

参考：[Tunnel API](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/)、
[源站 Access 校验](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)。
