"""Compute admission overlay; callers filter private registry fields before exposure."""
import copy
import math
import time

STATES = {'online', 'offline', 'reconnecting', 'degraded', 'paused', 'stale', 'disabled'}


def resolve_nodes(registry, snapshot, now=None):
    now = time.time() if now is None else now
    if not isinstance(snapshot,dict): snapshot = {}
    rows = snapshot.get('nodes', {})
    if not isinstance(rows,dict): rows = {}
    out = []
    for original in registry['nodes']:
        node = copy.deepcopy(original)
        if node.get('auto_managed'):
            status = rows.get(node['name'], {})
            if not isinstance(status, dict): status = {}
            checked = status.get('checked_at', 0)
            ttl = snapshot.get('max_stale_seconds', 120)
            numeric = all(type(v) in (int, float) and math.isfinite(v) for v in (now, checked, ttl))
            fresh = numeric and 0 <= now - checked <= ttl
            state = status.get('state', 'stale') if fresh else 'stale'
            if not isinstance(state, str) or state not in STATES:
                state = 'stale'
            admin = node.get('operator_enabled', True)
            node['enabled'] = bool(admin and fresh and state in ('online', 'paused'))
            node['connection_state'] = state if admin else 'disabled'
            node['last_checked_at'] = status.get('checked_at')
            node['last_online_at'] = status.get('last_online_at')
            node['capacity_pending'] = bool(status.get('capacity_pending'))
            node['sync_error'] = status.get('error')
            node['target_capacity'] = status.get('target_capacity')
        out.append(node)
    return out


def service_capacity(hardware, policy):
    cpu = max(0, int(hardware['cpus']) - int(policy.get('reserve_cpus', 2)))
    memory = max(0, int(hardware['memory_gib']) - int(policy.get('reserve_memory_gib', 8)))
    gpu = max(0, int(hardware['gpus']))
    for key, value in [('cpus', cpu), ('memory_gib', memory), ('gpus', gpu)]:
        maximum = policy.get('max_' + key)
        if maximum is not None:
            if key == 'cpus': cpu = min(cpu, int(maximum))
            elif key == 'memory_gib': memory = min(memory, int(maximum))
            else: gpu = min(gpu, int(maximum))
    if cpu < 1 or memory < 2:
        raise ValueError('insufficient_instance_capacity')
    return {'gpus': gpu, 'cpus': cpu, 'memory_gib': memory}


def transition(previous, observation, now, successes_required=2):
    good = bool(observation.get('ssh') and observation.get('runtime') and observation.get('proxy'))
    successes = previous.get('successes', 0) + 1 if good else 0
    failures = 0 if good else previous.get('failures', 0) + 1
    # First registration has no failed observation to recover from: a complete
    # live check is enough. Require repeated successes only after a real failure.
    ready = good and (not previous or previous.get('state') in ('online', 'paused') or successes >= successes_required)
    state = 'online' if ready else 'reconnecting' if good or failures < 2 else 'offline'
    if observation.get('ssh') and not good:
        state = 'degraded'
    return {'state': state, 'checked_at': now, 'last_online_at': now if ready else previous.get('last_online_at'),
            'successes': successes, 'failures': failures, 'error': observation.get('error')}
