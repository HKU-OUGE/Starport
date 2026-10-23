# 安装、配置与迁移

## 已有环境一键接入

```sh
./install.sh --check --legacy-root ../existing-tools
./install.sh --legacy-root ../existing-tools
```

引导输入现有私有接口，留空只绑定回环。脚本不创建 VPN 网络、不关闭防火墙，不自动申请云资源或下载大模型。

配置由安装时的实际环境生成，写在 XDG 用户配置目录的 `claude-isolation/config.json`；状态写在 XDG 用户状态目录的 `claude-isolation/`。两者都在源码仓库外，目录权限 0700，敏感文件 0600。

systemd 必须使用可定位的可执行路径，因此生成的本机 unit 使用实际绝对路径；这些 unit 不写入源码仓库。仓库文档和示例只使用相对路径。迁移到新目录后重新运行安装器，以更新生成的 unit。

## 软路由适配

在仓库外根据 `examples/router-profile.example.json` 准备适配配置。SSH 应使用已有别名，开启 BatchMode、StrictHostKeyChecking，禁止 agent 转发。管理 SSH 与出口密钥不在本仓库生成或保存。

```sh
./install.sh --legacy-root ../existing-tools --router-profile ../private/router-profile.json
```

现有适配器针对带 `mm_proxy_guard`、`mm_exit` 和专用端点路由脚本的部署。检测到不兼容的配置会拒绝应用，不能在未知拓扑上套用旧脚本。

## 空白机器

需要先按实际网络规划部署隔离路由、配置 SSH / 主机密钥、文件通道及所需计算服务，再用本仓库接入。云端实例、厂商账号、训练依赖和模型资产不属于此仓库的通用安装载荷。这里不把“面板已安装”描述为“所有后端已搭建”。

## 回滚

管理服务更新前保留 `.previous` 本地配置和 unit。停止面板后可恢复这些文件并执行用户级 daemon-reload。面板停止不会停止原有 Claude、软路由、训练、文件或桌面服务。订阅切换有独立私有备份和失败恢复逻辑。
