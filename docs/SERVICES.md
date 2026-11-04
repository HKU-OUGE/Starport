# 服务注册与生命周期

新安装统一使用以下名称：

| 位置 | 服务 | 作用 |
| --- | --- | --- |
| Linux systemd | `starport-console.service` | 会话控制台、缓存和成品读取 |
| Linux systemd | `starport-panel.service` | 服务、软路由与算力管理面板 |
| macOS launchd | `io.starport.starship` | 会话与后台执行 |
| macOS launchd | `io.starport.starship-activity` | 实时处理状态 |
| macOS launchd | `io.starport.starship-model-settings` | 模型与 effort 回执 |
| macOS launchd | `io.starport.starship-auth` | 原生登录状态维护 |

```sh
./bin/starport services
systemctl --user status starport-console.service starport-panel.service
./bin/starship status  # 在客户端执行
```

Windows 使用 `Starport` 任务文件夹中的同名用户任务。后台监督进程统一负责日志与重试，Windows 子进程加入专用 Job Object，停止该服务时一并回收其子进程。

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
| 用户 | starport-compute-sync.service | 服务器连接、资源规格与调度入口自动同步 |
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

## 服务器自动同步

后台每 30 秒检查已注册节点的 SSH、实际资源、执行环境和代理 HTTPS 出口；失联后连续两次成功才恢复入口。首次接入通过完整检查即可启用，后台重启沿用已有核验记录。失联或出口不可用时暂停该节点的新任务，保留运行中任务和资源预留。超过 120 秒未取得新状态时，调度按状态过期处理。

出口探测在 12 秒内最多尝试三次，使用两个 HTTPS 站点；所有尝试仍经过原代理并验证证书。单个站点偶发超时不会立即误判整个出口，全部失败仍暂停新任务。

已结束任务若缺少最后的清理回执，同步器会核对任务绑定、运行目录、进程和资源锁。只有确认执行环境已消失或停止、没有关联进程且资源锁空闲，才释放预留；日志和正式产物保留。

接收进程重启后，保护性暂停带有独立的恢复标记。同步器会在控制器空闲时核对遗留任务；远端已结束且清理完成的任务会补记真实失败原因并释放预留。若有完整结果包尚未收回，则保留等待结果回收。所有未结案记录处理完且连接、执行环境和代理检查通过后，仅解除自身创建的保护性暂停；之后发生的人工暂停不会被覆盖。

新版专用单节点执行器支持断线接管。远端独立监督进程托管原执行器，本机只负责提交、查询、取消和结果传输；控制连接关闭不会取消远端任务。启动使用原任务 ID 去重，接收进程重启后自动重新连接，已确认接管的任务可以解除本次启动产生的保护性暂停。手动暂停保持不变。

任务状态中的 `control_mode: durable-task-v1` 表示使用此协议，`control_connection` 显示当前连接状态；恢复成功后清除旧的重连提示，`control_reconnect_count` 仅记录本次控制进程内的历史重试次数。节点健康信息中的 `disconnect_survival` 表示新提交是否已采用该协议。运行中的旧任务保留原执行器，节点仅在空闲后加载新接收进程。此更新不改变标准队列或跨服务器任务。执行器接入约定见 [compute_runtime](../compute_runtime/README.md)。

结果先保存在远端并校验摘要，下载支持续传。远端完成并确认清理后即可释放资源，不等待控制端上线。近期日志使用 16 MiB 游标缓冲，远端另存有上限的压缩分段；超过保留范围会显示缺口。显式取消、训练期限、内存限制和执行器故障仍会停止任务；服务器重启后的计算恢复仍依赖训练程序的 checkpoint。网络出口继续由原有规则限制。

每个节点的健康接口同时返回 `ready` 和 `accepting_new_jobs`。选中的节点暂停时，提交返回 `new_jobs_paused` 和 `job_created: false`；资源确实不足时才返回容量原因。新版接收进程只在空闲且资源释放后加载，活动任务继续由原进程负责。

CPU、内存和 GPU 配额随实例规格更新，并保留配置中的系统余量和服务上限。旧接收进程有任务时继续运行，空闲后才加载新版；手动暂停不会被自动恢复。同步器重启沿用上次核验记录，过期记录标为待核对；监控保留离线节点，显示最后检查时间。

已有训练服务可从 `examples/compute-sync.example.json` 建立本地配置，再注册后台服务：

```sh
python3 -m isolation_toolkit.compute_sync --config ../config/compute-sync.json --install
```

该适配器连接现有 Linux 训练执行器。`dsw-pool.json` 中的 `operator_enabled: false` 表示人工停用；`enabled` 由同步器维护。日常暂停仍使用训练服务的暂停／恢复接口。未知节点不会自动加入，退役节点不会自动启用。

接入端需已有节点注册表、每节点的 `profile.json` / `capacity.json` / `operator.json`、任务状态目录，以及 `/health` 和 `/management/pause`、`/management/resume` 接口。此命令安装同步服务，训练执行器仍由原服务安装。

调度与监控读取同一份 `node-status.json`，通过以下接口处理状态过期；调度只向 `enabled` 节点提交，节点接口继续执行人工暂停检查。监控保留所有返回节点，只展示公开别名、资源额度、`connection_state` 和 `last_checked_at`，不直接返回原始注册表。

```python
from isolation_toolkit.compute_availability import resolve_nodes

nodes = resolve_nodes(registry, snapshot)
```
