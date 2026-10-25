# Starport

Linux 服务端包含会话控制台、后台缓存与管理面板，可独立安装。

```sh
./install.sh server --check
./install.sh server --ssh-host starship
./bin/starport urls
./bin/starport key
```

需要 Python 3.10+、Node.js 22.12+、npm、OpenSSH 和 systemd。安装器建立独立 Python 环境、按锁文件构建网页，注册 `starport-console.service` 与 `starport-panel.service`。

默认只绑定回环地址。使用 `--host` 绑定已有 VPN 的私有地址，管理面板另用 `--allowed-subnet` 指定允许访问的私有网段。公网 HTTPS 入口使用 `--public-origin` 配合现有 Access／Tunnel，后端额外监听回环端口 18883；访问码与 Origin／CSRF 校验仍然生效。

可选适配已有路由、文件和算力工具：

```sh
./install.sh server --legacy-root ../existing-tools --router-profile ../private/router-profile.json
```

配置示例见 [console.example.json](../examples/console.example.json)。软路由与训练执行器保留既有部署，通过管理面板接入；安装器不修改它们的网络策略或运行进程。
