"""Cross-platform install entry point; no packaging metadata or credentials."""
from pathlib import Path
import runpy,sys
if len(sys.argv)<2 or sys.argv[1] not in ('server','client'):
    raise SystemExit('Usage: python setup.py server|client [--check|--plan|--yes]')
role=sys.argv.pop(1)
path=Path(__file__).resolve().parent/role/'install.py'
sys.path.insert(0,str(path.parent))
runpy.run_path(str(path),run_name='__main__')
