// Display mapping for engine states. This module only translates fields the engine already
// reported (phase, observation_state, observation_reason, *_ms); it makes no decisions.

export const STATUS = {
  clear:     { key: 'clear',     label: 'Access clear',                 icon: '✓', short: 'Clear' },
  possible:  { key: 'possible',  label: 'Checking obstruction',         icon: '◔', short: 'Checking' },
  confirmed: { key: 'confirmed', label: 'Obstruction confirmed',        icon: '!', short: 'Confirmed' },
  resolved:  { key: 'resolved',  label: 'Incident resolved',            icon: '↺', short: 'Resolved' },
  unknown:   { key: 'unknown',   label: 'View unavailable or obscured', icon: '?', short: 'Unavailable' },
};

const PHASE_KEY = { CLEAR: 'clear', POSSIBLE: 'possible', CONFIRMED: 'confirmed', RESOLVED: 'resolved' };

/** observation_state = unknown takes precedence over the phase colour. */
export function statusKey(zoneState) {
  if (!zoneState) return null;
  if (zoneState.observation_state === 'unknown') return 'unknown';
  return PHASE_KEY[zoneState.phase] || 'unknown';
}

export function statusOf(zoneState) {
  const key = statusKey(zoneState);
  return key ? STATUS[key] : null;
}

export const REASON_TEXT = {
  positive: 'A watched object is inside the zone.',
  negative: 'No watched object is inside the zone.',
  initializing: 'Waiting for the first analyzed frame.',
  source_unavailable: 'The video frame was unavailable.',
  source_ended: 'The recording ended; the engine is no longer observing.',
  occluded: 'A person is blocking the view of the zone.',
  stale_input: 'The feed stalled.',
  observation_gap: 'Gap between analyzed frames; evidence restarted.',
  invalid_input: 'The engine rejected an input record.',
};

export const PHASE_TEXT = {
  CLEAR: 'CLEAR', POSSIBLE: 'POSSIBLE', CONFIRMED: 'CONFIRMED', RESOLVED: 'RESOLVED',
};

export const seconds = (ms) => (ms / 1000).toFixed(1);

/** "2.4 / 5.0 seconds of evidence" from the engine's positive_evidence_ms and the zone's confirm_ms. */
export function evidenceText(zoneState, zone) {
  const have = seconds(zoneState.positive_evidence_ms || 0);
  return zone ? `${have} / ${seconds(zone.confirm_ms)} seconds of evidence` : `${have} seconds of evidence`;
}

export function clearText(zoneState, zone) {
  const have = seconds(zoneState.negative_run_ms || 0);
  return zone ? `${have} / ${seconds(zone.resolve_ms)} seconds observed clear` : `${have} seconds observed clear`;
}

/** mm:ss.mmm for source-video time. */
export function clock(ms) {
  if (ms == null || Number.isNaN(ms)) return '--:--.---';
  const total = Math.max(0, Math.round(ms));
  const m = Math.floor(total / 60000);
  const s = Math.floor((total % 60000) / 1000);
  const r = total % 1000;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(r).padStart(3, '0')}`;
}

/** Short source-time label used on cards and markers, e.g. "12.4 s". */
export const at = (ms) => `${seconds(ms)} s`;

export function chip(zoneState, { large = false } = {}) {
  const status = statusOf(zoneState) || STATUS.unknown;
  const el = document.createElement('span');
  el.className = `chip chip-${status.key}${large ? ' chip-large' : ''}`;
  const icon = document.createElement('span');
  icon.className = 'chip-icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.textContent = status.icon;
  const label = document.createElement('span');
  label.textContent = status.label;
  el.append(icon, label);
  return el;
}
