# 用手机访问

```text
手机 ── VPN 或 HTTPS / Access ── A 的 Starport ── 受限 SSH ── B
```

先完成[本地验收](QUICKSTART.md)。外网入口配置在 A，B 不需要公开端口。

## 选择路径

| 方式 | 需要 | 适合 |
| --- | --- | --- |
| 私有 VPN | 手机与 A 加入同一虚拟局域网 | 已有稳定 VPN 的用户 |
| Cloudflare Access + Tunnel | 域名、Cloudflare 账号、允许登录的邮箱 | 希望手机直接用 HTTPS 打开 |

## VPN

在服务端安装配置里指定 A 的 VPN 私有地址和允许网段，再用 `starport.py urls` 查看入口。
不要绑定 `0.0.0.0`。用手机移动网络验证会话和附件；本机能打开不等于手机链路正常。

## Cloudflare

1. 选一个自己的 HTTPS 子域名，例如 `console.example.invalid` 是示例，不能实际使用。
2. 为该域名创建 **Access 自托管应用**，只允许指定邮箱／身份组。
3. 在 A 配置 `public_origin` 为这个完整 HTTPS 地址。
4. 创建 Tunnel，在 A 运行 connector，将该域名指向 `http://127.0.0.1:18883`。
5. 保留外部 Host 头；不要把它强制改成 `localhost`。
6. 手机测试：未授权账号被拒绝，授权账号通过后仍需 Starport 访问码。

安装命令中的配置形式：

```sh
python3 setup.py server --ssh-host starship --public-origin https://YOUR_CONSOLE_HOST
```

已有服务时按[更新流程](MAINTENANCE.md)修改配置，避免打断会话。
Tunnel 连接令牌只在 A 的受保护位置保存；不要输入会话或提交到仓库。
当前仓库提供源站接口，Cloudflare 资源通过官方控制台／工具创建。

不同使用者需要独立控制台时，为每套配置使用独立状态目录、端口、域名和 Access 应用。
**同一个控制台的邮箱白名单不等于按会话分权。**

## 验收与排障

| 项目 | 正常结果 |
| --- | --- |
| Access | 只允许指定身份登录 |
| Tunnel | connector 在线，转发到 A 的 18883 |
| Origin | 与 `public_origin` 精确一致 |
| 会话 | 能读消息、接收实时状态；经授权后测试一次发送 |
| 文件 | 小图片预览、PDF／视频下载成功 |
| 重启 | A 登录后 connector 和服务按选择恢复 |

`1033` 先查 connector，`origin_not_allowed` 先核对域名及 Host；不要为排障取消身份验证。
参考：[Cloudflare 私有 Web 应用](https://developers.cloudflare.com/cloudflare-one/setup/secure-private-apps/private-web-app/)。
