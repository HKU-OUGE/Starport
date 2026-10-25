from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parent/'bin/starport'),run_name='__main__')
