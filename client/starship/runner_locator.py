"""Find the device's Claude executable without relying on an interactive shell."""
from pathlib import Path
import json
import os
import platform
import re
import shutil


def usable(path, system):
    path = Path(path).expanduser()
    return path.is_absolute() and path.is_file() and (system == 'Windows' or os.access(path, os.X_OK))


def find_claude(home=None, system=None, env=None):
    home = Path(home) if home is not None else Path.home()
    system = system or platform.system()
    env = os.environ if env is None else env
    explicit = env.get('STARSHIP_CLAUDE_BIN')
    if explicit:
        if not usable(explicit, system):
            raise ValueError('runner_unavailable')
        return str(Path(explicit).expanduser().absolute())
    config = home / '.local/share/starship/runner.json'
    if config.is_file() and not config.is_symlink():
        try:
            saved = json.loads(config.read_text(encoding='utf-8')).get('claude_bin')
            if isinstance(saved, str) and usable(saved, system):
                return str(Path(saved).expanduser().absolute())
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if system == 'Darwin':
        root = home / 'Library/Application Support/Claude/claude-code'
        versions = sorted((p for p in root.iterdir() if re.fullmatch(r'\d+\.\d+\.\d+', p.name)),
                          key=lambda p: tuple(map(int, p.name.split('.'))), reverse=True) if root.is_dir() else []
        for version in versions:
            paths = list(version.glob('*/claude.app/Contents/MacOS/claude'))
            if len(paths) == 1 and usable(paths[0], system):
                return str(paths[0])
    candidates = [home / '.local/bin' / ('claude.exe' if system == 'Windows' else 'claude')]
    if system == 'Darwin':
        candidates += [Path('/opt/homebrew/bin/claude'), Path('/usr/local/bin/claude')]
    on_path = shutil.which('claude', path=env.get('PATH', os.defpath))
    if on_path:
        candidates.append(Path(on_path))
    for path in candidates:
        if usable(path, system):
            # Keep the stable launcher symlink so native auto-updates still work.
            return str(path.absolute())
    raise ValueError('runner_unavailable')
