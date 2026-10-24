// Right panel: current zone status, active incident and incident history.
// All values are read from engine records (states.jsonl / events.jsonl).

import { api } from './api.js';
import { COLORS } from './overlay.js';
import { REASON_TEXT, STATUS, at, chip, clearText, evidenceText, seconds, statusOf } from './status.js';

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

function objectsText(event) {
  const objects = event.evidence.objects || [];
  if (!objects.length) return 'none listed';
  return objects.map((o) => `${o.label} (${Math.round(o.confidence * 100)}%, ${Math.round(o.zone_overlap * 100)}% in zone)`).join(', ');
}

/** Evidence thumbnail: the source frame at evidence.t_ms with the zone and evidence boxes drawn on top. */
export function thumbnail(view, event, zone, { label } = {}) {
  const width = view.meta.width || 16;
  const height = view.meta.height || 9;
  const wrap = el('figure', 'thumb');
  wrap.style.aspectRatio = `${width} / ${height}`;
  if (view.meta.video_id) {
    const img = new Image();
    img.loading = 'lazy';
    img.alt = `Source frame at ${at(event.evidence.t_ms)}`;
    img.src = api.thumbUrl(view.meta.run_id, event.evidence.t_ms, 320);
    img.onerror = () => { img.remove(); wrap.classList.add('no-video'); };
    wrap.append(img);
  } else {
    wrap.classList.add('no-video');
    wrap.title = 'This run has no source video; showing evidence geometry only.';
  }
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', '0 0 1 1');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  if (zone) {
    const poly = document.createElementNS(svgNS, 'polygon');
    poly.setAttribute('points', zone.polygon.map(([x, y]) => `${x},${y}`).join(' '));
    poly.setAttribute('class', `thumb-zone thumb-${event.event_type === 'incident_resolved' ? 'resolved' : 'confirmed'}`);
    svg.append(poly);
  }
  for (const o of event.evidence.objects || []) {
    const [x1, y1, x2, y2] = o.bbox_xyxy;
    const rect = document.createElementNS(svgNS, 'rect');
    rect.setAttribute('x', x1); rect.setAttribute('y', y1);
    rect.setAttribute('width', x2 - x1); rect.setAttribute('height', y2 - y1);
    rect.setAttribute('class', 'thumb-box');
    svg.append(rect);
  }
  wrap.append(svg);
  const cap = el('figcaption', null, `${label ? `${label} · ` : ''}${at(event.evidence.t_ms)}`);
  wrap.append(cap);
  return wrap;
}

export class IncidentPanel {
  constructor({ statusRoot, activeRoot, historyRoot, onSeek }) {
    this.statusRoot = statusRoot;
    this.activeRoot = activeRoot;
    this.historyRoot = historyRoot;
    this.onSeek = onSeek;
    this.view = null;
    this.signature = null;
    this.cards = new Map();
  }

  clear(message) {
    this.view = null;
    this.signature = null;
    this.cards.clear();
    this.statusRoot.innerHTML = '';
    this.statusRoot.append(el('p', 'muted', message || 'Load or analyze a recording to see its status.'));
    this.activeRoot.innerHTML = '';
    this.activeRoot.append(el('p', 'muted', 'No active incident.'));
    this.historyRoot.innerHTML = '';
    this.historyRoot.append(el('p', 'muted', 'Confirmed findings will appear here.'));
  }

  load(view) {
    this.view = view;
    this.signature = null;
    this._renderHistory();
  }

  _renderHistory() {
    const view = this.view;
    this.historyRoot.innerHTML = '';
    this.cards.clear();
    if (!view.incidents.length) {
      this.historyRoot.append(el('p', 'muted', 'The engine confirmed no incidents in this run.'));
      return;
    }
    const finalStates = new Map(view.zoneIds.map((z) => [z, view.stateAt(z, Number.MAX_SAFE_INTEGER)]));
    for (const inc of view.incidents) {
      const zone = view.zoneById.get(inc.zone_id);
      const card = el('article', 'incident-card');
      const head = el('header', 'incident-head');
      head.append(el('strong', null, `Finding ${inc.number}`));
      const state = inc.resolved
        ? { phase: 'RESOLVED', observation_state: 'observed' }
        : { phase: 'CONFIRMED', observation_state: 'observed' };
      head.append(chip(state));
      card.append(head);

      const firstObject = inc.confirmed.evidence.objects?.[0]?.label || 'Watched object';
      const outcome = inc.resolved ? `Clear at ${at(inc.resolved.t_ms)}` : 'Still open at end of run';
      card.append(el('p', 'incident-summary', `${firstObject} · confirmed ${at(inc.confirmed.t_ms)} · ${outcome}`));

      const thumbs = el('div', 'thumbs');
      thumbs.append(thumbnail(view, inc.confirmed, zone, { label: 'Confirmed' }));
      if (inc.resolved) thumbs.append(thumbnail(view, inc.resolved, zone, { label: 'Resolved' }));

      const dl = el('dl', 'facts');
      const fact = (k, v) => { dl.append(el('dt', null, k), el('dd', null, v)); };
      fact('Zone', inc.zone_id);
      fact('Detected', objectsText(inc.confirmed));
      fact('First seen', at(inc.confirmed.first_seen_ms));
      fact('Confirmed', `${at(inc.confirmed.confirmed_at_ms)} (after ${seconds(inc.confirmed.confirmation_evidence_ms)} s of evidence)`);
      if (inc.resolved) {
        fact('Resolved', `${at(inc.resolved.resolved_at_ms)} (after ${seconds(inc.resolved.clear_evidence_ms)} s observed clear)`);
        fact('Duration', `${seconds(inc.resolved.resolved_at_ms - inc.confirmed.confirmed_at_ms)} s confirmed`);
      } else {
        const last = finalStates.get(inc.zone_id);
        const uncertain = last && last.incident_id === inc.incident_id && last.observation_state === 'unknown';
        fact('Resolved', uncertain
          ? 'Not resolved — the view became unavailable before a clear interval was observed'
          : 'Not resolved in this recording');
      }
      const details = el('details', 'incident-evidence');
      details.append(el('summary', null, 'View evidence and timing'), thumbs, dl);
      card.append(details);

      const actions = el('div', 'card-actions');
      const goConfirm = el('button', 'btn btn-small', `View at ${at(inc.confirmed.evidence.t_ms)}`);
      goConfirm.type = 'button';
      goConfirm.addEventListener('click', (e) => { e.stopPropagation(); this.onSeek(inc.confirmed.evidence.t_ms); });
      actions.append(goConfirm);
      if (inc.resolved) {
        const goResolve = el('button', 'btn btn-small btn-ghost', `Clear at ${at(inc.resolved.evidence.t_ms)}`);
        goResolve.type = 'button';
        goResolve.addEventListener('click', (e) => { e.stopPropagation(); this.onSeek(inc.resolved.evidence.t_ms); });
        actions.append(goResolve);
      }
      card.append(actions);
      this.cards.set(inc.incident_id, card);
      this.historyRoot.append(card);
    }
  }

  /** Refresh status + active incident for the playhead. Cheap when nothing changed. */
  update(tMs) {
    const view = this.view;
    if (!view) return;
    const indices = view.zoneIds.map((z) => view.stateIndexAt(z, tMs));
    const signature = indices.join(',');
    if (signature === this.signature) return;
    this.signature = signature;
    const states = view.zoneIds.map((z) => ({ zoneId: z, state: view.stateAt(z, tMs) }));
    this._renderStatus(states);
    this._renderActive(states);
    for (const [id, card] of this.cards) {
      const inc = view.incident(id);
      const live = tMs >= inc.confirmed.t_ms && (!inc.resolved || tMs < inc.resolved.t_ms);
      card.classList.toggle('live', live);
    }
  }

  _renderStatus(states) {
    this.statusRoot.innerHTML = '';
    for (const { zoneId, state } of states) {
      const zone = this.view.zoneById.get(zoneId);
      const card = el('div', 'zone-status');
      const title = el('div', 'zone-status-title');
      title.append(el('span', 'zone-name', zoneId));
      if (zone) title.append(el('span', 'muted small', `watching ${zone.candidate_labels.join(', ')}`));
      card.append(title);
      if (!state) {
        card.append(el('p', 'muted', 'No analysed frame yet at this point of the video.'));
        this.statusRoot.append(card);
        continue;
      }
      const status = statusOf(state);
      card.classList.add(`status-${status.key}`);
      card.append(chip(state, { large: true }));
      card.append(el('p', 'reason', REASON_TEXT[state.observation_reason] || state.observation_reason));
      if (state.observation_state === 'observed' && state.phase === 'POSSIBLE') {
        card.append(this._meter(evidenceText(state, zone), state.positive_evidence_ms, zone && zone.confirm_ms, 'possible'));
      } else if (state.observation_state === 'observed' && state.phase === 'CONFIRMED' && state.negative_run_ms > 0) {
        card.append(this._meter(clearText(state, zone), state.negative_run_ms, zone && zone.resolve_ms, 'resolved'));
      }
      if (state.observation_state === 'unknown' && state.phase === 'CONFIRMED') {
        card.append(el('p', 'badge badge-uncertain', '⚠ Incident still open — current view is uncertain. No resolution has been observed.'));
      }
      const meta = el('p', 'muted small engine-line');
      meta.textContent = `Engine: phase ${state.phase}, ${state.observation_state} (${state.observation_reason}) at ${at(state.t_ms)}`;
      card.append(meta);
      this.statusRoot.append(card);
    }
  }

  _meter(text, value, max, kind) {
    const wrap = el('div', 'meter');
    wrap.append(el('div', 'meter-text', text));
    if (max) {
      const bar = el('div', `meter-bar meter-${kind}`);
      bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', String(max));
      bar.setAttribute('aria-valuenow', String(Math.min(value, max)));
      bar.setAttribute('aria-label', text);
      const fill = el('span');
      fill.style.width = `${Math.min(100, (value / max) * 100)}%`;
      bar.append(fill);
      wrap.append(bar);
    }
    return wrap;
  }

  _renderActive(states) {
    this.activeRoot.innerHTML = '';
    const active = states.filter(({ state }) => state && state.phase === 'CONFIRMED' && state.incident_id);
    if (!active.length) {
      const lastResolved = states.find(({ state }) => state && state.phase === 'RESOLVED' && state.incident_id);
      if (lastResolved) {
        const inc = this.view.incident(lastResolved.state.incident_id);
        this.activeRoot.append(el('p', 'muted', inc && inc.resolved
          ? `No active incident. Incident ${inc.number} was resolved at ${at(inc.resolved.resolved_at_ms)}.`
          : 'No active incident.'));
      } else {
        this.activeRoot.append(el('p', 'muted', 'No active incident at this point of the video.'));
      }
      return;
    }
    for (const { zoneId, state } of active) {
      const inc = this.view.incident(state.incident_id);
      const zone = this.view.zoneById.get(zoneId);
      const card = el('div', 'active-incident');
      const head = el('div', 'incident-head');
      head.append(el('strong', null, inc ? `Incident ${inc.number} · ${zoneId}` : zoneId));
      head.append(chip({ phase: 'CONFIRMED', observation_state: 'observed' }));
      card.append(head);
      if (state.observation_state === 'unknown') {
        card.append(el('p', 'badge badge-uncertain', `⚠ Uncertain: ${REASON_TEXT[state.observation_reason] || state.observation_reason} The incident stays open until the engine observes the zone clear.`));
      }
      if (inc) {
        card.append(thumbnail(this.view, inc.confirmed, zone, { label: 'Evidence' }));
        const dl = el('dl', 'facts');
        dl.append(el('dt', null, 'Detected'), el('dd', null, objectsText(inc.confirmed)));
        dl.append(el('dt', null, 'Confirmed'), el('dd', null, at(inc.confirmed.confirmed_at_ms)));
        card.append(dl);
        const go = el('button', 'btn btn-small', `Jump to confirmation · ${at(inc.confirmed.evidence.t_ms)}`);
        go.type = 'button';
        go.addEventListener('click', () => this.onSeek(inc.confirmed.evidence.t_ms));
        card.append(go);
      }
      card.style.borderColor = COLORS.confirmed;
      this.activeRoot.append(card);
    }
  }
}

export { STATUS };
