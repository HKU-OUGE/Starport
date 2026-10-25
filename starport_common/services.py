"""User-session services for systemd, launchd and Windows Task Scheduler."""
from pathlib import Path
import json,os,platform,plistlib,shutil,subprocess,sys
from xml.etree import ElementTree as ET
from .runtime import config_home,secure_dir


def quote_unit(value):
    if any(c in str(value) for c in '\r\n\0'):raise ValueError('invalid_service_path')
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def windows_task(name,command,cwd,user='CURRENT_USER'):
    ns='http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('',ns)
    def add(parent,tag,text=None,**attrs):
        node=ET.SubElement(parent,'{'+ns+'}'+tag,attrs)
        if text is not None:node.text=text
        return node
    root=ET.Element('{'+ns+'}Task',version='1.2')
    trigger=add(add(root,'Triggers'),'LogonTrigger');add(trigger,'Enabled','true');add(trigger,'UserId',user)
    principal=add(add(root,'Principals'),'Principal',id='User');add(principal,'UserId',user);add(principal,'LogonType','InteractiveToken');add(principal,'RunLevel','LeastPrivilege')
    settings=add(root,'Settings');add(settings,'MultipleInstancesPolicy','IgnoreNew');add(settings,'DisallowStartIfOnBatteries','false');add(settings,'StopIfGoingOnBatteries','false');add(settings,'ExecutionTimeLimit','PT0S')
    action=add(add(root,'Actions',Context='User'),'Exec');add(action,'Command',str(command[0]));add(action,'Arguments',subprocess.list2cmdline(list(map(str,command[1:]))));add(action,'WorkingDirectory',str(cwd))
    return ET.tostring(root,encoding='unicode')


def definition(name,command,cwd,system=None):
    system=system or platform.system()
    if system=='Linux':
        return name+'.service',('[Unit]\nDescription='+name+'\nAfter=network-online.target\n\n[Service]\nType=simple\nExecStart='+' '.join(quote_unit(x) for x in command)+'\nWorkingDirectory='+str(cwd).replace('%','%%')+'\nRestart=on-failure\nRestartSec=5\nUMask=0077\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n\n[Install]\nWantedBy=default.target\n')
    if system=='Darwin':
        label='io.starport.'+name
        return label+'.plist',{'Label':label,'ProgramArguments':list(map(str,command)),'WorkingDirectory':str(cwd),'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':5,'Umask':0o077}
    if system=='Windows':return name+'.xml',windows_task(name,command,cwd)
    raise ValueError('unsupported_platform')


def register(definitions,no_start=False,system=None):
    system=system or platform.system()
    folder=(config_home()/'systemd/user' if system=='Linux' else Path.home()/'Library/LaunchAgents' if system=='Darwin' else config_home()/'starport/tasks')
    secure_dir(folder)
    for name,value in definitions.items():
        path=folder/name
        if path.is_symlink():raise ValueError('linked_service_definition')
        if path.exists():shutil.copy2(path,path.with_suffix(path.suffix+'.previous'))
        if system=='Darwin':path.write_bytes(plistlib.dumps(value))
        elif system=='Windows':
            user=subprocess.check_output(['whoami'],text=True).strip()
            xml=ET.fromstring(value)
            for node in xml.iter():
                if node.tag.endswith('}UserId') and node.text=='CURRENT_USER':node.text=user
            path.write_text(ET.tostring(xml,encoding='unicode'),encoding='utf-16')
        else:path.write_text(value,encoding='utf-8')
        path.chmod(0o600)
    if system=='Linux':
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable',*definitions],check=True)
        if not no_start:subprocess.run(['systemctl','--user','restart',*definitions],check=True)
    elif system=='Darwin':
        domain='gui/'+str(os.getuid())
        for name in definitions:
            if not no_start:
                subprocess.run(['launchctl','bootout',domain+'/'+name[:-6]],capture_output=True)
                subprocess.run(['launchctl','bootstrap',domain,str(folder/name)],check=True)
    else:
        for name in definitions:
            task='Starport\\'+name[:-4]
            subprocess.run(['schtasks.exe','/Create','/TN',task,'/XML',str(folder/name),'/F'],capture_output=True,check=True)
            if not no_start:subprocess.run(['schtasks.exe','/Run','/TN',task],capture_output=True,check=True)
    return folder


def prepare(name,command,cwd,state,python,system=None,interval=None):
    """A supervisor gives the same restart/log/periodic behavior on every OS."""
    folder=secure_dir(Path(state)/'services')
    config=folder/(name+'.json')
    config.write_text(json.dumps({'command':list(map(str,command)),'cwd':str(cwd),'log':str(folder/(name+'.log')),'interval':interval}),encoding='utf-8');config.chmod(0o600)
    runner=Path(__file__).with_name('supervise.py')
    return definition(name,[python,runner,config],cwd,system)
