"""Publication check for source files, independent from user message handling."""
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent
IGNORE={'.git','.venv','__pycache__','.pytest_cache'}
PATTERNS={
 'personal_home':re.compile(r'/(?:home|Users)/[A-Za-z0-9_.-]+/'),
 'private_key':re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----'),
 'cloud_access_key':re.compile(r'\b(?:AKIA[0-9A-Z]{16}|LTAI[0-9A-Za-z]{16,})\b'),
 'bearer_token':re.compile(r'Bearer\s+[A-Za-z0-9_-]{32,}'),
 'account_mail':re.compile(r'\b[A-Za-z0-9_.+-]+@(?![A-Za-z0-9.-]*\.invalid\b|example\.|localhost\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}


def audit():
    errors=[];count=0
    for p in ROOT.rglob('*'):
        if any(x in IGNORE for x in p.relative_to(ROOT).parts):continue
        if p.is_symlink():errors.append((str(p.relative_to(ROOT)),'symlink'));continue
        if not p.is_file():continue
        if p.name.endswith(('.key','.pem','.sqlite','.local.json')) or p.name in ('.env','access.key'):
            errors.append((str(p.relative_to(ROOT)),'private_runtime_file'));continue
        count+=1
        try:text=p.read_text()
        except UnicodeDecodeError:errors.append((str(p.relative_to(ROOT)),'unexpected_binary'));continue
        if p.name==Path(__file__).name:continue
        for name,pat in PATTERNS.items():
            if pat.search(text):errors.append((str(p.relative_to(ROOT)),name))
    for path,rule in errors:print(path+': '+rule)
    print(f'Checked {count} source files; {len(errors)} findings. No credential values are printed.')
    return not errors


if __name__=='__main__':sys.exit(0 if audit() else 1)
