from pathlib import Path
import sys
root=Path(__file__).resolve().parents[2]
if (root/'starport_common').is_dir():sys.path.insert(0,str(root))
from starport_common.runtime import fcntl,owned,secure_dir,desktop_home,process_options,stop_process,runner_environment,process_commands,WINDOWS,windows_open_regular,bounded_process,config_home,state_home
from starport_common.ipc import RPCServer,request as ipc_request,stdio as ipc_stdio
