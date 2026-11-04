# 让 Agent 帮你配置

Skills 是给 Agent 的操作手册。先下载仓库，让 Agent 在这个目录工作；不需要先安装额外插件。
人可以直接使用[交互教程](quickstart/index.html)。

## 第一次使用

把这段发给 Agent，并补上两台电脑的系统：

```text
请阅读当前 Starport 仓库的 AGENTS.md 和 skills/starport-onboard/SKILL.md。
我有两台新电脑：A 是 Linux，B 是 macOS。
请先检查现状，列出缺少的软件，再按实际能力完成安装、SSH 配对和验收。
登录启动选项请先问我；账号登录和管理员密码由我在本机窗口完成。
```

## 按任务选择

| Skill | 解决什么问题 |
| --- | --- |
| `starport-onboard` | 两台空电脑从准备到首个会话 |
| `starport-starship` | Mac／Windows／Linux 客户端安装、后台运行和权限 |
| `starport-pair` | 专用密钥、主机指纹、受限 SSH 配对 |
| `starport-network` | 网关边界、订阅、主备节点与断网验收 |
| `starport-remote` | 手机、VPN、Cloudflare Access 与 Tunnel |
| `starport-compute` | 接入已有隔离计算节点、依赖和数据 |
| `starport-maintain` | 排障、更新、卸载与有范围的清理 |

各目录都有 `SKILL.md` 和 Agent 展示元数据。支持技能目录的工具可按其机制安装；
安装到仓库外时，仍要告诉 Agent 当前 Starport checkout 的位置，指南和脚本以该目录为准。

## 可以自动做与需要本人做

| Agent 可以完成 | 本人／管理员完成 |
| --- | --- |
| 只读检查、生成配置、准备安装计划 | Claude 账号登录、操作系统权限窗口 |
| 已授权的安装与专用公钥配对 | 在 B 本机核对 SSH 主机指纹 |
| 测试连接、文件校验、故障定位 | 决定登录启动、公开入口、付费资源与数据删除范围 |

配套命令：`tools/preflight.py` 检查依赖，`tools/pair.py` 配对，
`tools/run_services.py` 手动启动。它们不会自动创建云实例或把机器配置成路由器。
