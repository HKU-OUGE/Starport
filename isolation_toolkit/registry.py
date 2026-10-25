"""Descriptions and exact mutation allowlist. Discovery never grants write access."""
import re

SPECIAL = {
    'starship.service': ('claude', 'Starship 客户端', '设备会话接口。'),
    'starport-console.service': ('claude', 'Starport 会话控制台', '消息、状态、成品及模型设置。'),
    'starport-panel.service': ('management', 'Starport 管理面板', '设备、服务、网络和算力入口。'),
    'starport-tunnel.service': ('network', 'Starport HTTPS 入口', '可选的受保护公网入口。'),
    'mm-isolation.service': ('network', '主机隔离守卫', '先于路由启动；停止服务也不应卸载防泄漏规则。'),
    'macmini-router.service': ('network', '虚拟路由器', '负责网桥、虚拟机及有线口的安全启动顺序。'),
    'zerotier-one.service': ('network', '远程管理网络', '管理面板的私有访问网络。'),
    'mac-claude-console.service': ('claude', 'Claude 会话控制台', '既有 Desktop Remote Control 与后台会话入口。'),
    'claude-isolation-panel.service': ('management', '隔离工具管理面板', '工具目录、服务注册、维护及监控汇总。'),
    'gpu-status-dashboard.service': ('management', '运算监控采集器', '复用既有资源、任务、传输、容量与清理统计。'),
    'mac-mini-smb.service': ('files', 'SMB 回环隧道', '启停或重启将影响正在使用该隧道的文件连接。'),
    'mac-mini-vnc.service': ('desktop', 'VNC 回环隧道', '可选远程桌面入口；停用不表示 Mac 应用已停止。'),
    'gpu-cache-cleanup.timer': ('storage', '已验证缓存清理定时器', '仅按既有清理逻辑回收；本面板不新增删除策略。'),
}
CONTROL = {
    'user:mac-mini-smb.service': ('start', 'stop', 'restart', 'enable', 'disable'),
    'user:mac-mini-vnc.service': ('start', 'stop', 'restart', 'enable', 'disable'),
    'user:gpu-status-dashboard.service': ('start', 'restart', 'enable'),
}
ACTION_NAMES = {'start': '启动', 'stop': '停止', 'restart': '重启', 'enable': '设为登录/开机启动', 'disable': '取消自动启动'}


def in_scope(name):
    return bool(re.fullmatch(r'(?:starship(?:-[A-Za-z0-9_-]+)?|starport-[A-Za-z0-9_-]+|gpu-[A-Za-z0-9_-]+|mac-mini-[A-Za-z0-9_-]+|macmini-router|mm-isolation|mac-claude-console|claude-isolation-panel|zerotier-one)\.(?:service|timer)', name))


def describe(name):
    if name in SPECIAL:
        group, title, note = SPECIAL[name]
    elif 'moonlight' in name:
        group, title, note = 'desktop', name, '既有受限串流基础设施；不会自动启动画面串流。'
    elif 'transfer' in name:
        group, title, note = 'files', name, '既有批量、分块、增量文件传输通道。'
    elif any(x in name for x in ('shared-data', 'artifacts', 'gpu-data', 'cleanup')):
        group, title, note = 'storage', name, '共享数据、结果或清理组件；运行中传输保持原流程。'
    elif 'egress' in name:
        group, title, note = 'network', name, '训练出口通道；异常需排查，不以主机直连替代。'
    else:
        group, title, note = 'compute', name, '训练或调度组件。维护前先排空相应工作负载。'
    return {'group': group, 'title': title, 'note': note}


TOOLS = [
    {'id': 'console', 'group': 'claude', 'title': 'Claude 会话', 'description': 'Remote Control、后台会话、消息、工具批准和媒体查看。', 'path': 'claude-console/', 'command': './mac-mini claude status'},
    {'id': 'router', 'group': 'network', 'title': '软路由检查', 'description': '服务、网桥、有线口、DNS、代理与断线守卫；可单独探测客户端 HTTPS。', 'path': 'router-status.sh', 'command': './mac-mini router-status --internet'},
    {'id': 'files', 'group': 'files', 'title': '批量与增量传输', 'description': '复用类型检查、分块、去重及 SHA-256 核验。', 'path': 'mac_files.py', 'command': './mac-mini files push ./project --project'},
    {'id': 'smb', 'group': 'files', 'title': 'SMB 文件浏览', 'description': '本机回环入口，供浏览与下载；上传继续使用受检工具。', 'path': 'mac-mini', 'command': './mac-mini files smb-status'},
    {'id': 'moonlight', 'group': 'desktop', 'title': 'Moonlight 画面', 'description': '图形登录会话中启动或停止既有受限串流，不改变路由策略。', 'path': 'moonlight-local/control.py', 'command': 'python3 ./moonlight-local/control.py start'},
    {'id': 'vnc', 'group': 'desktop', 'title': 'VNC 备用画面', 'description': '复用 SSH 回环隧道及既有客户端启动器。', 'path': 'open-mac-vnc.sh', 'command': './open-mac-vnc.sh'},
    {'id': 'apps', 'group': 'claude', 'title': '原生应用与会话诊断', 'description': '复用原生应用控制、窗口锁和只读会话诊断。', 'path': 'mac_apps.py', 'command': './mac-mini claude windows'},
    {'id': 'guard', 'group': 'files', 'title': '文件检查与文本转换', 'description': '匿名文本模式与保留字节的工程模式。网页用户消息只做简繁转换。', 'path': 'project_guard.py', 'command': './mac-mini privacy prepare ./input --staging-root ./staging'},
    {'id': 'training', 'group': 'compute', 'title': 'GPU / CPU 训练', 'description': '复用本机、专用服务器、标准和跨机后端；依赖层、资产及日志。', 'path': 'gpu-service/', 'command': './mac-mini manage monitor'},
    {'id': 'monitor', 'group': 'management', 'title': '资源与任务监控', 'description': '资源预算与预留、实际遥测、任务、传输、数据集、容量及缓存清理。', 'path': 'service-dashboard/', 'command': './mac-mini manage monitor'},
]

GROUPS = {'network': '网络与隔离', 'claude': 'Claude 会话', 'files': '文件传输', 'desktop': '远程桌面', 'compute': '训练计算', 'storage': '数据与清理', 'management': '管理与监控'}
