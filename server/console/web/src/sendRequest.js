// Reuse the identifier when a previous identical delivery is still uncertain.
export function sendRequest(previous, sessionId, message, newId, attachments=[]) {
  const same=previous?.session_id===sessionId && previous.message===message && JSON.stringify(previous.attachments||[])===JSON.stringify(attachments);
  const retry=same && ['submitting','unknown'].includes(previous.status);
  return {action:'send',session_id:sessionId,message,...(attachments.length?{attachments}:{}),operation_id:retry?previous.operation_id:newId()};
}
