"""Portable local primitives. No host environment crosses the SSH boundary."""
from pathlib import Path
import csv
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time

WINDOWS = os.name == 'nt'
_secured = set()
_acl_lock = threading.Lock()


def owned(stat):
    return WINDOWS or stat.st_uid == os.getuid()


def config_home():
    if WINDOWS:
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming'))
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))


def state_home():
    if WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))


def secure_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not owned(path.stat()):
        raise ValueError('private_directory_required')
    if not WINDOWS:
        path.chmod(0o700)
    else:
        with _acl_lock:
            key = str(path.resolve())
            if key not in _secured:
                result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, check=True)
                sid = next(csv.reader(io.StringIO(result.stdout.strip())))[1]
                if not sid.startswith('S-1-'):
                    raise ValueError('private_acl_unavailable')
                subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + sid + ':(OI)(CI)F'], capture_output=True, check=True)
                _secured.add(key)
    return path


if not WINDOWS:
    import fcntl
else:
    import msvcrt
    class fcntl:
        LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8
        @staticmethod
        def flock(file, flags):
            fd = file if isinstance(file, int) else file.fileno()
            pos = os.lseek(fd, 0, os.SEEK_CUR)
            try:
                while True:
                    try:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK if flags & 8 else msvcrt.LK_NBLCK, 1)
                        return
                    except OSError as exc:
                        if flags & (4 | 8):
                            raise BlockingIOError('local_lock_busy') from exc
                        time.sleep(.05)
            finally:
                os.lseek(fd, pos, os.SEEK_SET)


def desktop_home(home=None, system=None):
    home = Path(home or Path.home()); system = system or platform.system()
    if system == 'Darwin':
        return home / 'Library/Application Support/Claude'
    if system == 'Windows':
        return home / 'AppData/Roaming/Claude'
    return home / '.config/Claude'


def process_options():
    return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {'start_new_session': True}


def stop_process(process):
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)


def child_job(process):
    """Windows closes this job if the supervising task is terminated."""
    if not WINDOWS:return None
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                  ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(ctypes.Structure):_fields_=[(name,ctypes.c_uint64) for name in ('r','w','o','rb','wb','ob')]
    class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('pm',ctypes.c_size_t),('jm',ctypes.c_size_t),('peakp',ctypes.c_size_t),('peakj',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=(w.LPVOID,w.LPCWSTR);kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=(w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD)
    kernel.AssignProcessToJobObject.argtypes=(w.HANDLE,w.HANDLE)
    kernel.CloseHandle.argtypes=(w.HANDLE,)
    handle=kernel.CreateJobObjectW(None,None);limits=Extended();limits.basic.flags=0x2000
    if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,int(process._handle)):
        if handle:kernel.CloseHandle(handle)
        stop_process(process);raise OSError('child_job_unavailable')
    return lambda:kernel.CloseHandle(handle)


def process_commands():
    if WINDOWS:
        cmd = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
               'Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
        rows = json.loads(result.stdout or '[]')
        return rows if isinstance(rows, list) else [rows]
    return subprocess.check_output(['ps', '-axo', 'command='], text=True, timeout=5).splitlines()


def runner_environment():
    # This environment is created on Starship, never copied from Starport.
    allowed = ('PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','APPDATA','LOCALAPPDATA',
               'USERPROFILE','HOMEDRIVE','HOMEPATH','CLAUDE_CODE_GIT_BASH_PATH')
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(HOME=str(Path.home()), LANG='en_US.UTF-8', TZ='UTC', CLAUDE_CODE_ENTRYPOINT='sdk-py')
    env['PATH'] = str(Path.home() / '.local/bin') + os.pathsep + env.get('PATH', os.defpath)
    return env


def bounded_process(command, payload, timeout, limit):
    """Drain both pipes on all OSes; select() cannot read Windows pipe handles."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = bytearray(); too_large = threading.Event()
    def read(stream, keep):
        while True:
            part = stream.read(65536)
            if not part:
                break
            if keep:
                if len(output) + len(part) > limit:
                    too_large.set(); process.kill(); break
                output.extend(part)
    threads = [threading.Thread(target=read, args=(process.stdout, True), daemon=True),
               threading.Thread(target=read, args=(process.stderr, False), daemon=True)]
    for thread in threads: thread.start()
    def write():
        try: process.stdin.write(payload); process.stdin.close()
        except (OSError, ValueError): pass
    writer = threading.Thread(target=write, daemon=True); writer.start()
    try:
        code = process.wait(timeout=timeout)
        for thread in threads: thread.join(timeout=2)
        if too_large.is_set(): raise ValueError('remote_response_exceeds_limit')
        if any(thread.is_alive() for thread in threads): raise ValueError('connection_timeout_outcome_unknown')
        if code: raise ValueError('starship_connection_unavailable')
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise ValueError('connection_timeout_outcome_unknown') from None
    finally:
        if process.poll() is None: process.kill()
        process.wait(); writer.join(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr): stream.close()


def windows_open_regular(path):
    """Open once, verify the handle's final path, reject reparse redirection."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        st = part.lstat()
        if st.st_file_attributes & 0x400:
            raise ValueError('media_link_blocked')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path),0x80000000,1,None,3,0x00200000,None)
    if handle == ctypes.c_void_p(-1).value: raise OSError(ctypes.get_last_error(),'file_open_failed')
    final = kernel.GetFinalPathNameByHandleW
    final.argtypes = (wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD)
    close = kernel.CloseHandle; close.argtypes=(wintypes.HANDLE,)
    buf=ctypes.create_unicode_buffer(32768)
    long_name=kernel.GetLongPathNameW
    long_name.argtypes=(wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD)
    expected=ctypes.create_unicode_buffer(32768)
    try:
        expanded=long_name(str(path),expected,len(expected))
        if not expanded or expanded>=len(expected):raise ValueError('media_path_changed')
        count=final(handle,buf,len(buf),0)
        actual=buf.value
        if actual.startswith('\\\\?\\UNC\\'):actual='\\\\'+actual[8:]
        elif actual.startswith('\\\\?\\'):actual=actual[4:]
        if not count or count>=len(buf) or os.path.normcase(actual) != os.path.normcase(expected.value):
            raise ValueError('media_path_changed')
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY);handle=None
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):os.close(fd);raise ValueError('media_not_regular')
        return fd
    finally:
        if handle is not None:close(handle)
