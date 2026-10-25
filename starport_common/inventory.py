from pathlib import Path
import json,os,platform,re,subprocess


def user_services():
    system=platform.system();rows=[]
    if system=='Darwin':
        for path in (Path.home()/'Library/LaunchAgents').glob('io.starport.*.plist'):
            if path.is_symlink():continue
            label=path.stem
            result=subprocess.run(['launchctl','print','gui/'+str(os.getuid())+'/'+label],capture_output=True,text=True,timeout=5)
            active=result.returncode==0 and 'state = running' in result.stdout
            pid=re.search(r'\bpid = (\d+)',result.stdout)
            rows.append({'name':label,'running':active,'pid':int(pid[1]) if pid else 0,'manager':'launchd'})
    elif system=='Windows':
        script="Get-ScheduledTask -TaskPath '\\Starport\\' -ErrorAction SilentlyContinue | Select-Object TaskName,State | ConvertTo-Json -Compress"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=10)
        data=json.loads(result.stdout or '[]');data=data if isinstance(data,list) else [data]
        rows=[{'name':row['TaskName'],'running':row['State'] in (4,'Running'),'pid':0,'manager':'Task Scheduler'} for row in data]
    return rows
