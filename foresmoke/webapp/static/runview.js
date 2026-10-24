// Indexes a run's JSONL records by source t_ms for lookup at the playhead.
// Nothing here derives a phase: it only finds the record the engine wrote for a moment.

function lastAtOrBefore(items, tMs) {
  // items sorted by t_ms (file order is already non-decreasing); returns the last item <= tMs.
  let lo = 0;
  let hi = items.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid].t_ms <= tMs) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return found;
}

export class RunView {
  constructor({ meta, states, events, observations, zones }) {
    this.meta = meta;
    this.events = events;
    this.zoneConfig = zones;                       // the exact config the run used, or null
    this.zoneById = new Map((zones ? zones.zones : []).map((z) => [z.zone_id, z]));
    this.observations = observations.filter((o) => o.status === 'ok' && typeof o.t_ms === 'number');
    this.statesByZone = new Map();
    for (const s of states) {
      if (typeof s.t_ms !== 'number') continue;    // snapshots written before the first frame
      if (!this.statesByZone.has(s.zone_id)) this.statesByZone.set(s.zone_id, []);
      this.statesByZone.get(s.zone_id).push(s);
    }
    for (const id of this.zoneById.keys()) if (!this.statesByZone.has(id)) this.statesByZone.set(id, []);
    this.zoneIds = [...this.statesByZone.keys()];
    this.incidents = this._incidents();
    const lastState = Math.max(0, ...states.map((s) => s.t_ms || 0));
    this.durationMs = Math.max((meta.duration_s || 0) * 1000, lastState);
    const hz = meta.target_hz || 5;
    this.staleAfterMs = 1500 / hz;                 // hide boxes once the analysed frame is old
  }

  _incidents() {
    const byId = new Map();
    for (const e of this.events) {
      if (!byId.has(e.incident_id)) {
        byId.set(e.incident_id, { incident_id: e.incident_id, zone_id: e.zone_id, hazard_type: e.hazard_type,
          confirmed: null, resolved: null });
      }
      const item = byId.get(e.incident_id);
      if (e.event_type === 'incident_confirmed') item.confirmed = e;
      if (e.event_type === 'incident_resolved') item.resolved = e;
    }
    return [...byId.values()]
      .filter((i) => i.confirmed)
      .sort((a, b) => a.confirmed.t_ms - b.confirmed.t_ms)
      .map((item, index) => ({ ...item, number: index + 1 }));
  }

  /** The zone state record in effect at source time tMs (null before the first analysed frame). */
  stateAt(zoneId, tMs) {
    const list = this.statesByZone.get(zoneId) || [];
    const i = lastAtOrBefore(list, tMs);
    return i < 0 ? null : list[i];
  }

  stateIndexAt(zoneId, tMs) {
    return lastAtOrBefore(this.statesByZone.get(zoneId) || [], tMs);
  }

  /** The analysed observation shown at tMs, or null if none is recent enough. */
  observationAt(tMs) {
    const i = lastAtOrBefore(this.observations, tMs);
    if (i < 0) return null;
    const o = this.observations[i];
    return tMs - o.t_ms <= this.staleAfterMs ? o : null;
  }

  incident(id) { return this.incidents.find((i) => i.incident_id === id) || null; }

  /** Status segments for the timeline: consecutive records with the same display key. */
  segments(zoneId, keyOf) {
    const list = this.statesByZone.get(zoneId) || [];
    const out = [];
    let current = null;
    for (const s of list) {
      const key = keyOf(s);
      if (!current || current.key !== key) {
        if (current) current.end = s.t_ms;
        current = { key, start: s.t_ms, end: null, state: s, reason: s.observation_reason };
        out.push(current);
      }
    }
    if (current) current.end = Math.max(current.start, this.durationMs);
    return out.filter((seg) => seg.end > seg.start);
  }
}
