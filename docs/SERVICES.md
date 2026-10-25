# 服务注册与生命周期

新安装统一使用以下名称：

| 位置 | 服务 | 作用 |
| --- | --- | --- |
| Linux 用户服务 | `starport-console.service` | 会话控制台、缓存和成品读取 |
| Linux 用户服务 | `starport-panel.service` | 服务、软路由与算力管理面板 |
| Mac LaunchAgent | `io.starport.starship` | 会话与后台执行 |
| Mac LaunchAgent | `io.starport.starship-activity` | 实时处理状态 |
| Mac LaunchAgent | `io.starport.starship-model-settings` | 模型与 effort 回执 |
| Mac LaunchAgent | `io.starport.starship-auth` | 原生登录状态维护 |

```sh
./bin/starport services
systemctl --user status starport-console.service starport-panel.service
./bin/starship status  # 在客户端执行
```

## 既有服务适配

服务清单由当前 systemd 实时读取，不以历史说明里的数量为准。注册来源以 `system-unit/名称`、`user-unit/名称` 展示，源仓库不记录账户专属路径。

| 层级 | 组件 | 作用 |
| --- | --- | --- |
| 系统 | mm-isolation.service | 独立防泄漏守卫 |
| 系统 | macmini-router.service | 虚拟路由器安全启动顺序 |
| 系统 | mac-mini-transfer-*.service / timer | 批量分块文件通道 |
| 系统 | mac-mini-moonlight-*.service / timer | 受限串流基础设施与恢复 |
| 系统 | gpu-training、gpu-local-ea、gpu-manager 等 | 本机训练、环境与受控前端 |
| 用户 | mac-mini-smb.service / mac-mini-vnc.service | 回环文件共享与桌面隧道 |
| 用户 | mac-claude-console.service | Claude 网页控制台 |
| 用户 | gpu-*-api / egress / mac-link | 训练调度、网络出口与客户端入口 |
| 用户 | gpu-data、gpu-shared-data、gpu-artifacts | 上传、共享数据、执行中与完成产物读取 |
| 用户 | gpu-cache-cleanup.timer | 按原规则清理已验证临时缓存 |
| 用户 | gpu-status-dashboard.service | 既有只读监控 |
| 用户 | claude-isolation-panel.service | 旧版管理面板，兼容保留 |
| 客户端 | local.session-service LaunchAgent | 旧版会话服务，迁移时不自动重启 |

## 查看实际注册

```sh
./bin/starport services
systemctl --user status claude-isolation-panel.service
systemctl --user is-enabled claude-isolation-panel.service
systemctl status macmini-router.service mm-isolation.service
loginctl show-user "$USER" --property=Linger
```

`enabled` 说明已注册自动启动；`active/running` 说明当前进程在运行；`active/exited` 可以是正常的 oneshot 服务。`static` 定时任务执行单元和 `inactive` 可选组件不自动算失败。`activating/auto-restart` 要检查实际原因。

用户服务默认随登录启动。已启用 linger 时可以在系统启动后保持用户服务管理器；本安装器读取其状态，不自动更改系统策略。

## 注册 / 更新 / 卸载管理服务

```sh
./install.sh --legacy-root ../existing-tools
systemctl --user status claude-isolation-panel.service
# 停止并取消本面板自动启动，保留全部配置和原工具
systemctl --user disable --now claude-isolation-panel.service
```

重复安装会备份上次本地配置和管理 unit，再更新本面板。不会运行旧路由安装器，也不批量 enable 所有已发现服务。有些服务和 NetworkManager profile 被故意禁用，由路由生命周期接管，不能为了“全绿”直接启用。

## 网页维护范围

- SMB、VNC：启动、停止、重启、自动启动开关。
- 监控采集器：启动、重启、自动启动；这些操作不重启训练任务。
- 训练核心、路由器、隔离守卫、其他进程：只读。
- 每个操作先显示目标与影响，确认时再次核对服务状态，按操作 ID 去重。

维护训练核心前，按该后端的已有流程暂停新入口，等待任务和资源释放，再在维护窗口操作。不能用本面板重启绕开这些步骤。
