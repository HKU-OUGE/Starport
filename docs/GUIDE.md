# 日常使用

连接你的 AI、设备与算力。

## 三个入口

1. 管理面板：通过 `./bin/starport urls` 查看本机安装地址，默认端口 18882。
2. Claude 会话：复用原控制台，通常端口 18881。支持已开启 Remote Control 的 Desktop 会话及独立后台会话。未开启 Remote Control 的桌面历史不自动变成可写会话。
3. 运算监控：复用既有只读采集器，通常端口 18880。远程手机可在管理面板的“任务与存储”中读取汇总，不必额外开放采集器端口。

本机和手机均需输入访问码。手机必须先加入部署时指定的私有网络。服务绑定特定地址，不绑定所有网卡。

## 常用操作

在 Starport 目录中查询状态与入口：

```sh
./bin/starport status
./bin/starport services
./bin/starport router --internet
```

以下命令在已接入的工具包根目录执行：

```sh
./mac-mini ssh
./mac-mini router-status --internet
./mac-mini files status
./mac-mini files smb-status
./mac-mini files push ./project --project
./mac-mini files push ./notes.md
./mac-mini files pull batch-example --destination ./download
./mac-mini claude status
./mac-mini claude windows
python3 ./moonlight-local/control.py start
python3 ./moonlight-local/control.py stop
./open-mac-vnc.sh
```

文件上传复用原检查、分块、增量与摘要校验。普通文本模式转换简体到繁体，工程模式默认保留字节；需要繁体工程时使用该版本支持的显式转换选项。SMB 供浏览和下载，向客户端上传业务文件仍走原受检入口。

网页中用户输入的消息只进行简繁转换，不另加语义敏感词拦截。此规则不改变文件上传检查或网络边界。客户端内容、日志和文档不能自行授权工具发送消息或执行维护。

## 查询训练状态

管理面板复用采集器快照，不重新访问云控制台、不创建训练任务。GPU / CPU / 内存的“预留”是调度预算；不等于瞬时硬件利用率。任务结果、依赖层、共享资产、数据 ID 与上传机制仍由原训练服务提供。

快照时间较旧、节点不就绪和入口暂停要分别查看。不要因为监控失联就取消或重新提交任务。

## 故障顺序

- 管理页打不开：确认私有网络、安装地址和管理服务；登录页与上游 Claude 登录是两层认证。
- Claude 会话不显示：检查原控制台以及 Mac 会话服务；不要盲目重启正在工作的会话进程。
- Mac 无网络：先执行软路由检查，分别看有线口、虚拟机、守卫、DNS、代理和 HTTPS。
- SMB / VNC 失败：先确认 Mac SSH，再维护对应回环隧道。重启隧道会中断其当前连接。
- 训练节点重试：查看 API 与 egress 两个组件；API 可读不代表任务出口可用。
- 磁盘紧张：查看正式数据、临时传输、重复归档及清理证据；管理面板不直接删除文件。
