ENDPOINTS={'session','settings','activity','documents','artifacts','artifact-sync'}


def command(endpoint,platform='posix'):
    if endpoint not in ENDPOINTS:raise ValueError('ssh_command_blocked')
    if platform=='windows':
        return 'cmd.exe /d /s /c ""%USERPROFILE%\\.local\\share\\starship\\rpc.cmd" '+endpoint+'"'
    if platform!='posix':raise ValueError('invalid_client_platform')
    return '"$HOME/.local/share/starship/rpc" '+endpoint
