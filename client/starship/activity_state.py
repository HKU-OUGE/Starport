"""Content-free activity metadata derived from reported session events."""
from collections import OrderedDict, deque
from datetime import datetime
import re
import time


def timestamp(value):
    try:
        return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def tool_name(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,100}', value) else 'Tool'


class Activity:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.phase = 'synchronizing'
        self.active = False
        self.started = None
        self.finished = None
        self.duration = None
        self.basis = 'observed'
        self.last_event = None
        self.sequence = 0
        self.assistant_sequence = 0
        self.user_sequence = 0
        self.tools = OrderedDict()
        self.waiting = {}
        self.queued = 0
        self.users = deque(maxlen=128)
        self.connected = False
        self.worker = 'unknown'
        self.meta_at = None
        self.confirmed_at = None
        self.error = False
        self.stream_connected = False
        self.blocks = {}
        self.saw_boundary = False

    def begin(self, at, reported=True):
        if not self.active:
            self.started = at
            self.finished = self.duration = None
            self.active = True
            self.basis = 'reported' if reported and self.saw_boundary else 'observed'
            if reported and not self.saw_boundary:
                self.basis = 'at_least'
            self.queued = max(0, self.queued - 1)

    def metadata(self, item):
        self.meta_at = self.clock()
        self.connected = item.get('connection_status') == 'connected'
        self.worker = item.get('worker_status', 'unknown')
        self.error = False
        if self.connected:
            self.confirmed_at = self.meta_at
        if self.connected and self.worker == 'idle' and self.last_event and self.meta_at - self.last_event > 8000:
            # The remote idle state is authoritative; repeated historical user
            # blocks cannot establish an unconsumed queue after a finished turn.
            self.queued = 0
            if self.phase == 'queued':
                self.phase = 'complete' if self.finished is not None else 'idle'
        if self.connected and self.worker == 'idle' and self.active and self.last_event and self.meta_at - self.last_event > 8000:
            self.active = False
            self.finished = self.last_event
            self.duration = max(0, self.finished - self.started) if self.started is not None else None
            self.phase = 'queued' if self.queued else 'complete'
        if self.worker == 'running' and not self.active and self.phase not in {'complete', 'error'}:
            self.begin(self.meta_at, False)
            self.phase = 'working'

    def ingest(self, envelope, live=False):
        p = envelope.get('payload', envelope)
        if not isinstance(p, dict):
            return
        try:
            seq = int(envelope.get('sequence_num') or 0)
        except (TypeError, ValueError):
            return
        if seq and seq <= self.sequence:
            return
        self.sequence = max(self.sequence, seq)
        if p.get('parent_tool_use_id'):
            return
        reported = timestamp(envelope.get('created_at') or p.get('timestamp'))
        at = reported or self.clock()
        kind = p.get('type')
        if kind in {'user', 'assistant'}:
            blocks = (p.get('message') or {}).get('content', [])
            if isinstance(blocks, str):
                blocks = [{'type': 'text'}]
            if not isinstance(blocks, list):
                return
            blocks = [b for b in blocks if isinstance(b, dict)]
            if kind == 'user':
                results = [b for b in blocks if b.get('type') == 'tool_result']
                for block in results:
                    self.tools.pop(block.get('tool_use_id'), None)
                if results:
                    if self.active:
                        self.phase = 'tool' if self.tools else 'working'
                elif any(b.get('type') in {'text', 'image'} for b in blocks):
                    uid = p.get('uuid') or ('seq-' + str(seq) if seq else None)
                    if uid and uid in self.users:
                        return
                    if uid:
                        self.users.append(uid)
                    self.user_sequence = max(self.user_sequence, seq)
                    self.queued = min(20, self.queued + 1)
                    self.saw_boundary = True
                    if not self.active:
                        self.phase = 'queued'
            else:
                was_active = self.active
                self.begin(at, reported is not None)
                if not was_active and reported is not None:
                    self.basis = 'at_least'
                self.assistant_sequence = max(self.assistant_sequence, seq)
                for block in blocks:
                    kind_b = block.get('type')
                    if kind_b == 'tool_use':
                        tid = block.get('id')
                        if isinstance(tid, str) and len(tid) <= 160:
                            self.tools[tid] = tool_name(block.get('name'))
                            while len(self.tools) > 64:
                                self.tools.popitem(last=False)
                        self.phase = 'tool'
                    elif kind_b in {'text', 'thinking', 'redacted_thinking'}:
                        # A complete block is historical evidence, not proof
                        # that its generation phase is still in progress.
                        self.phase = 'tool' if self.tools else 'working'
            self.last_event = at
        elif kind == 'stream_event':
            event = p.get('event') or {}
            if not isinstance(event, dict):
                return
            etype = event.get('type')
            delta = event.get('delta') or {}
            if etype == 'message_start':
                self.begin(at, reported is not None)
                self.phase = 'working'
            elif etype == 'content_block_start':
                block = event.get('content_block') or {}
                btype = block.get('type')
                self.blocks[event.get('index')] = btype
                self.begin(at, reported is not None)
                self.phase = {'thinking': 'thinking', 'redacted_thinking': 'thinking',
                              'text': 'writing', 'tool_use': 'preparing_tool'}.get(btype, 'working') if live else 'working'
            elif etype == 'content_block_delta':
                dtype = delta.get('type')
                phase = {'thinking_delta': 'thinking', 'text_delta': 'writing', 'input_json_delta': 'preparing_tool'}.get(dtype)
                if phase:
                    self.begin(at, reported is not None)
                    self.phase = phase if live else 'working'
            elif etype == 'content_block_stop':
                self.blocks.pop(event.get('index'), None)
                self.phase = 'tool' if self.tools else 'working'
            elif etype == 'message_stop':
                self.phase = 'tool' if self.tools else 'working'
            else:
                return
            self.last_event = at
        elif kind == 'result':
            self.active = False
            self.finished = at
            duration = p.get('duration_ms')
            # Bridge completion markers can contain zero as an unspecified
            # duration. Preserve the observed interval instead of erasing it.
            self.duration = duration if isinstance(duration, (int, float)) and 0 < duration <= 30*86400000 else None
            if self.duration is not None:
                self.started = at - self.duration
                self.basis = 'reported'
            self.tools.clear(); self.waiting.clear(); self.blocks.clear()
            self.phase = 'error' if p.get('is_error') else ('queued' if self.queued else 'complete')
            self.last_event = at
            self.saw_boundary = True
        elif kind == 'control_request' and envelope.get('source') == 'worker':
            request = p.get('request') or {}
            rid = p.get('request_id')
            if request.get('subtype') == 'can_use_tool' and isinstance(rid, str) and len(rid) <= 160:
                self.waiting[rid] = tool_name(request.get('tool_name'))
                self.phase = 'waiting_input' if self.waiting[rid] == 'AskUserQuestion' else 'waiting_approval'
                self.last_event = at
        elif kind in {'control_response', 'control_cancel_request'}:
            response = p.get('response') or {}
            self.waiting.pop(response.get('request_id', p.get('request_id')), None)
            if not self.waiting and self.active:
                self.phase = 'tool' if self.tools else 'working'
            self.last_event = at
        elif kind == 'system' and p.get('subtype') == 'status':
            if p.get('status') == 'compacting':
                self.begin(at, reported is not None)
                self.phase = 'compacting'; self.last_event = at
            elif p.get('status') is None and self.active:
                self.phase = 'working'; self.last_event = at

    def snapshot(self):
        now = self.clock()
        fresh = bool(self.meta_at and now - self.meta_at < 25000 and not self.error)
        phase, active = self.phase, self.active
        end = self.finished
        if not fresh:
            phase, active = 'synchronizing', False
        elif not self.connected:
            phase, active = 'disconnected', False
        elif self.worker == 'requires_action':
            phase = 'waiting_input' if 'AskUserQuestion' in self.waiting.values() else 'waiting_approval'
            active = False
        elif self.worker == 'idle' and self.active and (not self.last_event or now - self.last_event > 8000):
            phase, active = 'idle', False
            end = self.meta_at
        elif phase == 'synchronizing':
            phase = 'idle' if self.worker == 'idle' else 'working'
        sampled = now if fresh and self.connected else (self.confirmed_at or self.last_event or now)
        elapsed = self.duration if self.duration is not None and not self.active else max(0, (end or sampled) - self.started) if self.started is not None else None
        return {'phase': phase, 'active': bool(active and phase not in {'waiting_input', 'waiting_approval'}),
                'timer_running': bool(fresh and self.connected and self.active),
                'turn_started_at_ms': self.started, 'finished_at_ms': end, 'elapsed_ms': elapsed,
                'timer_basis': self.basis, 'sampled_at_ms': sampled, 'last_event_at_ms': self.last_event,
                'last_sequence': self.sequence, 'last_user_sequence': self.user_sequence,
                'last_assistant_sequence': self.assistant_sequence,
                'queued': self.queued, 'tool_name': next(reversed(self.tools.values()), None) if self.tools else None,
                'connected': self.connected, 'stream_connected': self.stream_connected,
                'fresh': fresh, 'source': 'remote_control_events'}
