# 两台电脑怎么配对

```text
A：生成登录密钥          B：安装 Starship
   私钥留下 ── 公钥 ───→ 只授权固定 RPC 命令
   核对指纹 ←─ 主机指纹 ─ 在 B 本机读取
   固定主机密钥 ── SSH ─→ 验证服务
```

命令都在仓库目录运行。Windows 使用 `py -3`，下面的大写占位符需要换成实际值。

## 1. A 生成专用密钥

```sh
ssh-keygen -t ed25519 -f ~/.ssh/starport_starship -C starport
```

Windows PowerShell：

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\starport_starship" -C starport
```

已有同名文件时先检查，不要覆盖。将 **`starport_starship.pub`** 复制到 B。
没有 `.pub` 后缀的是私钥，只留在 A。需要无人值守时，选择能在服务会话中使用的 SSH agent，
或仅用于受限接口的专用密钥；带口令的密钥不能依赖网页弹出解锁。

## 2. B 添加受限公钥

先装好 Starship，再启用系统 SSH 服务。Mac 见[远程登录](MAC.md)；Windows 见
[官方 OpenSSH 安装说明](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)。
Windows 建议使用非管理员的本地 Starship 用户；管理员用户默认读取另一个共享公钥文件。

```sh
python3 tools/pair.py authorize --public-key starport_starship.pub
python3 tools/pair.py authorize --public-key starport_starship.pub --apply
```

工具保留原有公钥，只增加当前专用密钥的固定命令规则。如果已有不同权限的同一密钥，先人工核对。

## 3. 在 B 本机读取主机指纹

Linux／Mac：

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Windows PowerShell：

```powershell
ssh-keygen -lf "$env:ProgramData\ssh\ssh_host_ed25519_key.pub"
```

如系统要求读取权限，由设备本人在本机授权。复制输出中的 `SHA256:…`。
这是 **B 的 SSH 主机身份**，不是上一步 A 的公钥指纹。

## 4. A 固定主机身份

`DEVICE_IP` 是 B 在私有管理路径上的 IPv4 地址，`HOST_FINGERPRINT` 是刚才本机核对的指纹。

```sh
python3 tools/pair.py pin --host DEVICE_IP --fingerprint HOST_FINGERPRINT
python3 tools/pair.py pin --host DEVICE_IP --fingerprint HOST_FINGERPRINT --apply
python3 tools/pair.py config --host DEVICE_IP --user DEVICE_USER
```

把最后输出的 `Host starship` 段合并到 A 的 SSH 配置：POSIX 为 `~/.ssh/config`，
Windows 为 `$env:USERPROFILE\.ssh\config`。已有同名段时合并，不要重复添加。
工具单独使用 `starport_known_hosts`，不改其他服务器的信任记录。

## 5. 验证

```sh
python3 tools/pair.py verify
ssh starship whoami
```

第一条应返回 `ok: true`；第二条应返回 `ssh_command_blocked`。
B 为 Windows 时，第一条加 `--client-platform windows`。

| 报错 | 先检查 |
| --- | --- |
| 连接超时 | B 是否唤醒、SSH 是否启动、预定管理路径是否放行 |
| 主机密钥不匹配 | 在 B 本机重新核对；不要关闭校验 |
| Permission denied | 用户短名称、公钥安装位置、私钥及其解锁方式 |
| RPC 不可用 | B 上 `python3 starship.py status` |
| 能连但未登录 | B 上完成 Claude 登录；不要传递 A 的浏览器 Cookie |
