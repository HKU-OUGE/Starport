# Starship

在 Mac 上接續 Claude Desktop Remote Control，或運行獨立的後臺 Claude Code 會話。

```sh
./install.sh client --check
./install.sh client
./bin/starship status
```

需要 macOS、Python 3.10+ 和已登入的 Claude Code。先在 Mac 完成原生登入，再開啓系統“遠程登錄”。Desktop 會話需開啓 Remote Control；後臺會話與 Desktop 歷史分開管理。重啓後首次登入 macOS，之後鎖屏仍可運行。

安裝器註冊 `io.starport.starship` 及狀態、模型設置、登入維護三個輔助 LaunchAgent。客戶端只監聽本用戶的 Unix socket，由 Starport 通過固定 SSH 入口調用。連接示例見 [SSH 配置](../examples/ssh.example.conf)。

更新前結束後臺任務並卸載 Starship 的 LaunchAgent，再重新運行安裝器；運行中的安裝會被拒絕覆蓋。已有 Desktop 會話與其原生憑證不遷移到服務端。
