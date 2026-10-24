// Incident timeline. Every segment and marker is positioned by source-video t_ms.

import { STATUS, at, statusKey, REASON_TEXT } from './status.js';

const pct = (ms, total) => `${Math.max(0, Math.min(100, (ms / total) * 100))}%`;

function niceStep(totalMs) {
  const seconds = totalMs / 1000;
  for (const step of [1, 2, 5, 10, 15, 30, 60, 120, 300]) if (seconds / step <= 10) return step * 1000;
  return 600000;
}

export class Timeline {
  constructor({ root, onSeek }) {
    this.root = root;
    this.onSeek = onSeek;
    this.totalMs = 0;
    this.playhead = null;
    this.track = null;
  }

  clear(message = 'Load a run to see its timeline.') {
    this.root.innerHTML = '';
    const empty = document.createElement('p');
    empty.className = 'muted timeline-empty';
    empty.textContent = message;
    this.root.append(empty);
    this.playhead = null;
  }

  render(view, totalMs) {
    this.totalMs = Math.max(totalMs, 1);
    this.root.innerHTML = '';
    const tracks = document.createElement('div');
    tracks.className = 'tl-tracks';

    for (const zoneId of view.zoneIds) {
      const row = document.createElement('div');
      row.className = 'tl-row';
      const name = document.createElement('div');
      name.className = 'tl-label';
      name.textContent = zoneId;
      const lane = document.createElement('div');
      lane.className = 'tl-lane';
      for (const seg of view.segments(zoneId, statusKey)) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `tl-seg seg-${seg.key}`;
        b.style.left = pct(seg.start, this.totalMs);
        b.style.width = pct(seg.end - seg.start, this.totalMs);
        const label = `${STATUS[seg.key].label} from ${at(seg.start)} to ${at(seg.end)}`
          + (seg.key === 'unknown' ? ` (${REASON_TEXT[seg.reason] || seg.reason})` : '');
        b.title = label;
        b.setAttribute('aria-label', `${zoneId}: ${label}. Jump to ${at(seg.start)}.`);
        b.addEventListener('click', (e) => { e.stopPropagation(); this.onSeek(seg.start); });
        lane.append(b);
      }
      row.append(name, lane);
      tracks.append(row);
    }

    const markerRow = document.createElement('div');
    markerRow.className = 'tl-row tl-markers-row';
    const markerLabel = document.createElement('div');
    markerLabel.className = 'tl-label';
    markerLabel.textContent = 'Incidents';
    const markers = document.createElement('div');
    markers.className = 'tl-lane tl-markers';
    for (const inc of view.incidents) {
      const end = inc.resolved ? inc.resolved.t_ms : this.totalMs;
      const span = document.createElement('div');
      span.className = `tl-span${inc.resolved ? '' : ' open'}`;
      span.style.left = pct(inc.confirmed.t_ms, this.totalMs);
      span.style.width = pct(end - inc.confirmed.t_ms, this.totalMs);
      markers.append(span);
      markers.append(this._marker('confirmed', inc.confirmed.t_ms,
        `Incident ${inc.number} confirmed at ${at(inc.confirmed.t_ms)}`, inc.confirmed.evidence.t_ms, `${inc.number}`));
      if (inc.resolved) {
        markers.append(this._marker('resolved', inc.resolved.t_ms,
          `Incident ${inc.number} resolved at ${at(inc.resolved.t_ms)}`, inc.resolved.evidence.t_ms, '↺'));
      }
    }
    if (!view.incidents.length) {
      const none = document.createElement('span');
      none.className = 'tl-none';
      none.textContent = 'No incidents confirmed in this run';
      markers.append(none);
    }
    markerRow.append(markerLabel, markers);
    tracks.append(markerRow);

    const axisRow = document.createElement('div');
    axisRow.className = 'tl-row tl-axis-row';
    const axisLabel = document.createElement('div');
    axisLabel.className = 'tl-label tl-axis-caption';
    axisLabel.textContent = 'source s';
    const axis = document.createElement('div');
    axis.className = 'tl-lane tl-axis';
    const step = niceStep(this.totalMs);
    for (let t = 0; t <= this.totalMs + 1; t += step) {
      const tick = document.createElement('span');
      tick.className = 'tl-tick';
      tick.style.left = pct(t, this.totalMs);
      tick.textContent = `${Math.round(t / 1000)}`;
      axis.append(tick);
    }
    axisRow.append(axisLabel, axis);
    tracks.append(axisRow);

    this.playhead = document.createElement('div');
    this.playhead.className = 'tl-playhead';
    this.playhead.setAttribute('aria-hidden', 'true');
    this.track = tracks;
    tracks.append(this.playhead);
    tracks.addEventListener('click', (e) => {
      const lane = tracks.querySelector('.tl-lane');
      const r = lane.getBoundingClientRect();
      if (e.clientX < r.left) return;
      this.onSeek(((e.clientX - r.left) / r.width) * this.totalMs);
    });
    this.root.append(tracks);
  }

  _marker(kind, tMs, label, seekMs, text) {
    const m = document.createElement('button');
    m.type = 'button';
    m.className = `tl-marker marker-${kind}`;
    m.style.left = pct(tMs, this.totalMs);
    m.title = label;
    m.setAttribute('aria-label', `${label}. Jump to that moment.`);
    m.textContent = text;
    m.addEventListener('click', (e) => { e.stopPropagation(); this.onSeek(seekMs); });
    return m;
  }

  setTime(tMs) {
    if (!this.playhead || !this.track) return;
    const lane = this.track.querySelector('.tl-lane');
    if (!lane) return;
    const offset = lane.offsetLeft;
    const width = lane.clientWidth;
    this.playhead.style.left = `${offset + (Math.max(0, Math.min(tMs, this.totalMs)) / this.totalMs) * width}px`;
  }
}
