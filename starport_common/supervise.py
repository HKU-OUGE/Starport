"""Run only an installer-generated command, preserving a bounded local log."""
from pathlib import Path
import json,os,signal,subprocess,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from starport_common.runtime import process_options,stop_process,child_job


def main():
    config=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    process=None;stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
        if process and process.poll() is None:stop_process(process)
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
    try:
        while not stopped:
            log=Path(config['log'])
            if log.exists() and log.stat().st_size>4*1024*1024:log.replace(log.with_suffix('.previous'))
            with log.open('ab') as out:
                process=subprocess.Popen(config['command'],cwd=config['cwd'],stdout=out,stderr=out,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},**process_options())
                close_job=child_job(process)
                try:process.wait()
                finally:
                    if close_job:close_job()
            delay=config.get('interval') or 5
            for _ in range(int(delay)*5):
                if stopped:break
                time.sleep(.2)
    finally:stop()


if __name__=='__main__':main()
