export const phaseNames = {
  thinking: 'Thinking · 思考中', writing: 'Responding · 正在回复',
  working: 'Working · 处理中', tool: 'Running tool · 执行工具',
  preparing_tool: 'Preparing tool · 准备工具', compacting: 'Compacting · 整理上下文',
  waiting_approval: '等待你的批准', waiting_input: '等待你的回答', queued: '已接收，等待处理',
  complete: '本轮已完成', idle: '等待输入', error: '本轮执行失败', stopped: '已停止',
  disconnected: '连接已断开', synchronizing: '正在同步状态', history: '历史记录',
};

export function duration(ms) {
  if (!Number.isFinite(ms)) return '—';
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return seconds < 3600 ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` :
    `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export function fallbackActivity(session, detail, now) {
  const status = session?.status;
  let phase = ({running:'working', starting:'working', waiting_approval:'waiting_approval', interrupted:'disconnected', connecting:'synchronizing'})[status] || status || 'synchronizing';
  const events = detail?.events || [];
  const lastResult = events.findLastIndex(e => e.role === 'result');
  const turn = events.slice(lastResult + 1);
  const pending = detail?.pending || [];
  if (pending.length) phase = pending.some(p => p.tool === 'AskUserQuestion') ? 'waiting_input' : 'waiting_approval';
  const active = ['running', 'starting'].includes(status);
  const calls = new Map();
  for (const event of turn) {
    if (event.role === 'tool' && event.tool_id) calls.set(event.tool_id, event.tool_name || event.text);
    if (event.role === 'tool_result') calls.delete(event.tool_id);
  }
  if (active && !pending.length) phase = detail?.partial ? 'writing' : calls.size ? 'tool' : 'working';
  const start = turn.find(e => ['user','assistant','tool'].includes(e.role) && Number.isFinite(Date.parse(e.time)));
  const startTime = start ? Date.parse(start.time) : null;
  return {phase, active, timer_running:active, elapsed_ms:active && startTime !== null ? Math.max(0, now-startTime) : null,
    timer_basis:'at_least', tool_name:active ? [...calls.values()].at(-1) : null, fresh:true,
    _receivedAt:now, source:'session_status'};
}

export function presentation(session, detail, state, now) {
  const same = state?.session === session?.id;
  const value = same && state.value ? state.value : fallbackActivity(session, detail, now);
  const age = Math.max(0, now - (state?.receivedAt || now));
  const stale = same && Boolean(state.error || (state.value && (!state.value.fresh || age > 25000)));
  const phase = stale ? (value.phase === 'disconnected' ? 'disconnected' : 'synchronizing') : value.phase;
  const runningClock = !stale && value.timer_running;
  const elapsed = Number.isFinite(value.elapsed_ms) ? value.elapsed_ms + (runningClock && same && state.value ? age : 0) : null;
  return {...value, phase, label:phaseNames[phase] || phaseNames.synchronizing,
    elapsed, stale, age, timerLabel:value.timer_basis === 'reported' ? '本轮' : value.timer_basis === 'at_least' ? '至少' : '已观察',
    detail:stale ? '状态暂未确认，已保留上次记录' : value.tool_name || ''};
}

const rejectedCodes=new Set(['login_required','session_busy','source_session_still_running','invalid_message','invalid_operation','session_not_found','project_unavailable','permission_no_longer_pending','remote_session_disconnected','remote_session_not_bound','remote_session_not_found','remote_login_required','remote_login_expired','remote_login_or_device_verification_required','remote_permission_requires_official_ui','authentication_required','invalid_session','preview_confirmation_required']);
export function deliveryStatus(code) {return rejectedCodes.has(code)?'rejected':'unknown';}
