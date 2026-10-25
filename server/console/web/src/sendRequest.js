// Reuse the identifier when a previous identical delivery is still uncertain.
export function sendRequest(previous, sessionId, message, newId) {
  const same=previous?.session_id===sessionId && previous.message===message;
  const retry=same && ['submitting','unknown'].includes(previous.status);
  return {action:'send',session_id:sessionId,message,operation_id:retry?previous.operation_id:newId()};
}
