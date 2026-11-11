/** Per-tab UI recovery hints only. Server state and session authorization win. */
export function recoveryHint(state) {
  if (!state.call || ['idle', 'ended'].includes(state.phase)) return null;
  return { id: state.call.id, connectedAt: state.connectedAt, muted: !!state.muted,
    output: state.output || 'default', speaker: !!state.speaker,
    presentation: state.presentation, savedAt: Date.now() };
}
export function validHint(value, now = Date.now()) {
  return value && typeof value.id === 'string' && ['full', 'minimized'].includes(value.presentation) &&
    Number.isFinite(value.savedAt) && now - value.savedAt < 86400000 && value.savedAt <= now + 60000 &&
    (value.connectedAt == null || Number.isFinite(value.connectedAt) && value.connectedAt <= now) ? value : null;
}
