# 软路由、隧道和订阅

## 三系统的路由虚拟机

Starport 可运行在 Linux、macOS 或 Windows。路由与防泄漏规则集中在独立 OpenWrt／Linux 网关中，管理端通过 SSH 调用固定执行器。

| 宿主 | 虚拟机方式 | 隔离设备接入 |
| --- | --- | --- |
| Linux | KVM / QEMU | 专用网桥或网口直通 |
| macOS | UTM / QEMU | 专用有线网口桥接或 USB 网卡直通 |
| Windows | Hyper-V / QEMU | 独立虚拟交换机与专用有线网口 |

LAN 网口只服务隔离设备，WAN 只提供网关代理端点连接。管理路径与设备出口分开；设备不能通过宿主网络共享、第二网卡或 IPv6 获得旁路。代理停止、全部节点失效、路由虚拟机停止三种情况下，都应实测断网；恢复后再测试 DNS、HTTPS 和开机顺序。

[UTM 网络模式说明](https://docs.getutm.app/settings-qemu/devices/network/network/)提供桥接及仅主机网络；[Hyper-V 虚拟交换机](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/create-a-virtual-switch-for-hyper-v-virtual-machines)用于配置独立网络。网口接管取决于宿主、网卡与虚拟化驱动，安装控制台不会自动改变这些设置。

## 从 Mac / Windows 管理既有网关

在已经部署双守卫、代理与端点路由的 Linux 网关中安装固定执行器：

```sh
python3 router/install.py --check
sudo python3 router/install.py --config ../private/gateway-profile.json
```

这个配置沿用网关已有的 `legacy_root`、`state_dir`、`router.ssh` 和 `router.host_exit_dir`。执行器不建立虚拟机、不覆盖现有防火墙；它复用本项目已验证的出口事务和回滚逻辑。

给网关配置专用 SSH 身份，仅允许固定命令 `sudo -n starport-router-executor`，并在网关端为该命令配置受限授权。然后在 Starport 接入 [router-remote.example.json](../examples/router-remote.example.json)：

```sh
python3 setup.py server --router-profile ../private/router-remote.json
```

订阅解析、主备节点选择和计划预览仍在面板完成。应用计划时，Linux 网关重新校验旧配置摘要、不可修改字段和节点端点；先关闭出口，再切换，失败恢复或保持关闭。操作者配置、SSH 密钥与节点凭据不进入 Starship。


## 关系

客户端有线口 → 无主机地址的客户端网桥 → 路由虚拟机 → 代理 TUN → 已登记的加密节点。

另有独立管理网桥、虚拟任务网桥和受控 WAN 网桥。主机的 SSH 管理走管理路径；客户端没有任意访问主机服务的权限。文件和桌面通道复用各自受限网络空间。

真实接口、管理地址、SSH 别名和出口配置在外部安装配置及现有受保护配置目录中。源码只记录通用组件名称。

## 启动顺序

主机守卫 → 受控网桥与已批准上联 → 路由虚拟机与 guest 守卫 → 代理与 DNS → 客户端有线口 → 依赖通道。

某些 NetworkManager 连接和虚拟机 autostart 故意关闭，由生命周期脚本统一控制。发生连接问题时先检查，不能卸载防火墙或增加直连回退。

## 订阅管理

支持 HTTPS 订阅和粘贴导入：

- Clash / Mihomo 的 `proxies` YAML；提供者的 `payload` 节点列表。
- 明文或 Base64 的 SS、VMess、VLESS、Trojan 节点 URI。
- 当前出口使用 TCP，UDP 型协议会显示不支持，不会为兼容订阅放开 UDP。

读取节点字段及其精确匹配的 `hosts` 入口别名，将别名解析为受控公共 IPv4，不导入全局 hosts。订阅里的 rules、DNS、脚本、代理提供者文件路径、控制器设置、外部监听和直连策略不会进入有效配置。证书校验保持开启。当前适配器沿用既有三个受控节点槽位：按勾选顺序选择主用、第一备用和第二备用；固定模式只使用首选，自动回退模式按顺序选择可用节点。

订阅 URL 和节点密码存放在仓库外的私有状态目录。浏览器只看到名称、协议和支持状态。HTTPS 获取不跟随重定向，拒绝私有 / 回环订阅地址，并固定解析后的公共 IPv4；需要重定向的服务请填写最终 HTTPS 地址。

## 应用计划

1. 导入或更新订阅。此步骤不会切换正在运行的网络。
2. 勾选三个节点和模式，生成预览计划。保留当前规则、DNS、监听与独立守卫。
3. 若配置没有变化，直接返回无需切换。
4. 若需要修改出口，在本机运行页面给出的相对命令：

```sh
./bin/starport apply-exit PLAN_ID --check
./bin/starport apply-exit PLAN_ID
```

系统会弹出管理员授权窗口。第一个命令只测试候选配置和防火墙语法；第二个命令才应用。计划有时间限制，并核对生成预览后的配置是否变化。

执行器保存私有备份，先关闭受控出口，保留独立守卫，再更新 guest / host 的精确节点白名单及专用路由，加载代理并做 HTTPS 检查。失败尝试恢复原配置；恢复失败时保持出口关闭。整个过程不重启路由虚拟机、不停止训练进程，但网络切换会短暂影响已有连接，长任务需要自行容忍重连。

首版不在网页进程内持有 root 权限。因此手机可以管理订阅、选择节点和准备计划，涉及防火墙的最终应用需要本机管理员操作。不要把管理员密码输入网页。

## 验收

面板的路由探测复用原只读检查，可选从客户端做 HTTPS 探测。它不能替代代理全故障、IPv6 / UDP / 直连阻断和实际冷启动验收。新增出口应在合适窗口完成这些检查后再作为正式出口。

上游格式参考：
- https://wiki.metacubex.one/api/
- https://wiki.metacubex.one/config/proxy-groups/fallback/
- https://wiki.metacubex.one/config/proxies/ss/
