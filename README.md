# Starport

![Starport — 连接你的 AI、设备与算力](docs/assets/starport-cover.svg)

[快速开始](#快速开始) · [使用指南](docs/GUIDE.md) · [网络与出口](docs/NETWORK.md) · [安装与迁移](docs/INSTALL.md)

Starport 将 AI 会话、设备连接、网络出口与计算资源汇聚到一个工作台。查看状态、传输文件、选择节点，让每一项工作有清晰的入口。

## 快速开始

先运行只读检测，再运行引导安装：

```sh
./install.sh --check --legacy-root ../existing-tools
./install.sh --legacy-root ../existing-tools
```

需要 Python 3.10+、PyYAML、Linux systemd、iproute2，以及已有工具包。引导脚本让你选择已有的私有网络接口；留空只开放本机回环。它只注册和启动 `claude-isolation-panel.service`，不会重装软路由、修改隔离规则或重启训练服务。

如果系统缺少 PyYAML：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python install.py --legacy-root ../existing-tools
```

安装后：

```sh
./bin/starport urls      # 实际访问地址
./bin/starport key       # 仅在本机显示访问码
./bin/starport status    # 服务、工具、网络与开机启动概况
```

如果已有会话控制台的访问码文件满足私有权限要求，自动复用访问码；否则在外部配置目录生成新码。浏览器登录仍独立，访问码不进入 URL。

## 你的工作台

| 模块 | 能力 |
| --- | --- |
| 总览 | 服务数量、异常、GPU 预算与预留、磁盘可用空间、常用入口 |
| 服务注册 | 系统 / 用户服务、运行状态、自动启动、注册来源、依赖和重试次数 |
| 软路由 | 网桥、接口、SSH、DNS、代理及守卫检查；可选客户端 HTTPS 探测 |
| 订阅与节点 | HTTPS 订阅、Clash / Mihomo YAML、节点 URI / Base64；选择主用和两个备用节点 |
| 任务与存储 | 复用既有监控的节点、进行中任务、传输、容量及清理记录 |
| 工具目录 | 文件、SMB、VNC、Moonlight、原生应用、Claude、训练接口的入口 |
| 指南 | 安装、服务注册、网络与日常维护说明 |

管理接口只允许固定的 SMB、VNC 和监控服务操作。路由、训练、出口守卫默认只读。操作有预览、状态变化检查、防重复执行和审计记录。

订阅配置不会覆盖路由规则、DNS、监听地址和隔离守卫。当前 TCP 出口支持 SS、VMess、VLESS、Trojan 的受支持传输；UDP 型节点不自动开放。新增出口先生成计划，再在本机运行计划命令，弹出管理员授权；面板不收集管理员密码。

## 兼容现有安装

主命令为 `./bin/starport`。原 `./bin/claude-isolation` 命令、`claude-isolation-panel.service` 服务名及外部配置目录继续兼容；更名不改变现有路由、凭据或训练环境。

## 部署方式

Starport 通过适配器接入已有工具和服务。安装器负责管理面板的配置与注册；空白主机需要先部署路由、设备连接和所需计算环境。详细步骤见[安装与迁移](docs/INSTALL.md)。

新机安装、真实管理员切换出口、代理全故障和整机冷启动都需要各自验收；“安装成功”不代替这些测试。

## 目录

```text
bin/                 命令行入口
isolation_toolkit/   服务发现、状态适配、认证和订阅解析
web/                 无前端构建依赖的管理界面
docs/                运维与迁移指南
examples/            仅含示例值的外部配置模板
tests/               边界、订阅、计划及服务操作测试
install.sh           一键引导入口
install.py           配置生成与管理服务注册
apply_exit.py        经过本机管理员授权的出口计划执行器
```

文档：[日常使用](docs/GUIDE.md)、[服务注册](docs/SERVICES.md)、[软路由](docs/NETWORK.md)、[安装迁移](docs/INSTALL.md)、[信息边界](docs/SECURITY.md)。

## 开发检查

Markdown 渲染测试需要 Node.js；面板运行不依赖 Node.js。

```sh
python3 -m unittest discover -s tests -v
node --test tests/markdown.test.cjs
python3 audit_repository.py
```
