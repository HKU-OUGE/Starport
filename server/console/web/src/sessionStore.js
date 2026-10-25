// Conversation snapshots live only in memory and are cleared on logout.
export function applyDelta(current, delta, sessionId) {
  if (!delta.delta || delta.session_id !== sessionId) throw Error('delta_session_mismatch');
  if (!delta.reset && (!current || current._cursor !== delta.base_cursor)) throw Error('delta_cursor_mismatch');
  if (!delta.reset && delta.unchanged) {
    if (delta.cursor !== current._cursor) throw Error('delta_cursor_mismatch');
    return current;
  }
  const next = {...(delta.reset ? {} : current), ...delta.changes};
  for (const key of delta.removed_fields || []) delete next[key];
  const events = new Map((delta.reset ? [] : current.events).map(e => [e._event_id, e]));
  for (const id of delta.removed || []) events.delete(id);
  for (const event of delta.events || []) events.set(event._event_id, event);
  if (!Array.isArray(delta.order) || new Set(delta.order).size !== delta.order.length ||
      delta.order.some(id => !events.has(id)) || next.session?.id !== sessionId) throw Error('delta_resync_required');
  const ordered = delta.order.map(id => events.get(id));
  next.events = current && ordered.length === current.events.length && ordered.every((e, i) => e === current.events[i]) ? current.events : ordered;
  next._cursor = delta.cursor;
  return next;
}

export class SessionStore {
  constructor(maxSessions = 8, maxBytes = 8 * 1024 * 1024) {
    this.maxSessions = maxSessions; this.maxBytes = maxBytes; this.entries = new Map(); this.bytes = 0;
  }
  get(id) {
    const entry = this.entries.get(id);
    if (entry) {this.entries.delete(id); this.entries.set(id, entry);}
    return entry?.value || null;
  }
  set(id, value) {
    if (this.entries.get(id)?.value === value) return;
    const size = new TextEncoder().encode(JSON.stringify(value)).length;
    this.bytes -= this.entries.get(id)?.size || 0; this.entries.delete(id);
    if (size > this.maxBytes) return;
    this.entries.set(id, {value, size}); this.bytes += size;
    while (this.entries.size > this.maxSessions || this.bytes > this.maxBytes) {
      const first = this.entries.keys().next().value;
      this.bytes -= this.entries.get(first).size; this.entries.delete(first);
    }
  }
  clear() {this.entries.clear(); this.bytes = 0;}
}
