"""Owned startup pauses. Node handlers serialize these calls with their API lock."""
import json
import os
from pathlib import Path
import time
import uuid

VERSION = 1
OWNER = 'startup_recovery'


def read(root):
    try:
        return json.loads((Path(root) / 'management.json').read_text())
    except FileNotFoundError:
        return {'accepting_new_jobs': True}


def write(root, value):
    path = Path(root) / 'management.json'
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    with temp.open('x') as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def unsettled(records, terminal):
    return any(row.get('status') not in terminal or
               not row.get('allocation_released', False) for row in records)


def guard_startup(root, records, terminal):
    """Pause for uncertain records; retain a pre-existing manual pause verbatim."""
    before = read(root)
    if unsettled(records, terminal) and before.get('accepting_new_jobs', True):
        write(root, {'accepting_new_jobs': False, 'pause_owner': OWNER,
                     'recovery_id': uuid.uuid4().hex, 'paused_at': time.time()})
    # Repeated starts preserve an owned pause until a healthy remote check.


def set_admission(root, accepting):
    """An explicit pause/resume always invalidates earlier automatic ownership."""
    write(root, {'accepting_new_jobs': bool(accepting)})


def resume_startup(root, records, terminal, busy, recovery_id, attached=()):
    before = read(root)
    uncertain = [row for row in records if
                 (row.get('status') not in terminal or not row.get('allocation_released', False))
                 and row.get('id') not in attached]
    if ((busy and not attached) or uncertain or not recovery_id or
            before.get('pause_owner') != OWNER or
            before.get('recovery_id') != recovery_id or
            before.get('accepting_new_jobs', True)):
        return False
    write(root, {'accepting_new_jobs': True, 'startup_recovered_at': time.time()})
    return True
