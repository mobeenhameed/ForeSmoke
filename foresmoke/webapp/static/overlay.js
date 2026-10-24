// Canvas drawing for zone outlines, detections and the annotation draft.

import { STATUS, statusOf } from './status.js';

export const COLORS = {
  clear: '#22a35a',
  possible: '#e0a000',
  confirmed: '#e04848',
  resolved: '#3f7fe8',
  unknown: '#8a93a3',
  saved: '#f2f4f7',
  draft: '#38d0ff',
  invalid: '#ff4d6d',
};

const toPx = (rect, [x, y]) => [rect.x + x * rect.w, rect.y + y * rect.h];

function polygonPath(ctx, rect, points) {
  ctx.beginPath();
  points.forEach((p, i) => {
    const [x, y] = toPx(rect, p);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.closePath();
}

function tag(ctx, x, y, text, { fill = 'rgba(12,16,22,0.82)', color = '#fff', border = null } = {}) {
  ctx.font = '600 12px Inter, system-ui, sans-serif';
  const w = ctx.measureText(text).width + 10;
  const h = 18;
  const tx = Math.max(2, Math.min(x, ctx.canvas.width / (window.devicePixelRatio || 1) - w - 2));
  const ty = Math.max(2, y - h);
  ctx.fillStyle = fill;
  ctx.fillRect(tx, ty, w, h);
  if (border) { ctx.strokeStyle = border; ctx.lineWidth = 1; ctx.strokeRect(tx + 0.5, ty + 0.5, w - 1, h - 1); }
  ctx.fillStyle = color;
  ctx.fillText(text, tx + 5, ty + 13);
}

export function drawFrameBackdrop(ctx, rect, text) {
  ctx.fillStyle = '#151a22';
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  ctx.strokeStyle = '#2b3340';
  ctx.setLineDash([4, 4]);
  ctx.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.w - 1, rect.h - 1);
  ctx.setLineDash([]);
  ctx.fillStyle = '#7f8a9b';
  ctx.font = '500 13px Inter, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(text, rect.x + rect.w / 2, rect.y + 22);
  ctx.textAlign = 'left';
}

/** A saved or analysed zone outline, coloured by the engine status when one is known. */
export function drawZone(ctx, rect, zone, zoneState, { dashed = false } = {}) {
  const status = zoneState ? statusOf(zoneState) : null;
  const color = status ? COLORS[status.key] : COLORS.saved;
  polygonPath(ctx, rect, zone.polygon);
  ctx.fillStyle = `${color}2e`;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = color;
  ctx.setLineDash(dashed ? [8, 6] : []);
  ctx.stroke();
  ctx.setLineDash([]);
  const top = zone.polygon.reduce((best, p) => (p[1] < best[1] ? p : best), zone.polygon[0]);
  const [x, y] = toPx(rect, top);
  const label = status ? `${zone.zone_id} · ${status.label}` : `${zone.zone_id} · saved zone`;
  tag(ctx, x - 4, y - 6, label, { fill: status ? color : 'rgba(12,16,22,0.82)',
    color: status && status.key === 'possible' ? '#1b1300' : '#fff' });
}

function box(ctx, rect, det, { color, width = 2, dashed = false, label }) {
  const [x1, y1, x2, y2] = det.bbox_xyxy;
  const [ax, ay] = toPx(rect, [x1, y1]);
  const [bx, by] = toPx(rect, [x2, y2]);
  ctx.lineWidth = width;
  ctx.strokeStyle = color;
  ctx.setLineDash(dashed ? [5, 4] : []);
  ctx.strokeRect(ax, ay, bx - ax, by - ay);
  ctx.setLineDash([]);
  if (label) tag(ctx, ax, ay - 2, label, { border: color });
}

const sameBox = (a, b) => a.label === b.label && a.bbox_xyxy.every((v, i) => v === b.bbox_xyxy[i]);

/**
 * Detections at the playhead. Engine candidates (from the zone state) are highlighted in the
 * status colour with their zone_overlap. Other boxes are shown only if their label is watched
 * by a zone, unless debug is on.
 */
export function drawDetections(ctx, rect, observation, zoneStates, watched, debug) {
  const candidates = [];
  for (const { state } of zoneStates) {
    if (!state) continue;
    const status = statusOf(state);
    for (const c of state.candidates || []) candidates.push({ c, color: COLORS[status.key] });
  }
  if (observation) {
    for (const det of observation.detections) {
      if (candidates.some(({ c }) => sameBox(c, det))) continue;
      const relevant = watched.has(det.label);
      if (!relevant && !debug) continue;
      box(ctx, rect, det, {
        color: relevant ? '#f2f4f7' : '#9aa3b2',
        width: relevant ? 2 : 1,
        dashed: !relevant,
        label: `${det.label} ${det.confidence.toFixed(2)}${det.track_id ? ` #${det.track_id}` : ''}`,
      });
    }
  }
  for (const { c, color } of candidates) {
    box(ctx, rect, c, {
      color, width: 3,
      label: `${c.label} ${c.confidence.toFixed(2)} · ${Math.round(c.zone_overlap * 100)}% in zone`,
    });
  }
}

/** The polygon being drawn, with numbered handles and failing edges in red. */
export function drawDraft(ctx, rect, draft, validation) {
  const pts = draft.points;
  if (pts.length >= 3) {
    polygonPath(ctx, rect, pts);
    ctx.fillStyle = validation.ok ? 'rgba(56,208,255,0.18)' : 'rgba(255,77,109,0.14)';
    ctx.fill();
  }
  const n = pts.length;
  for (let i = 0; i < n; i += 1) {
    if (n < 2 || (n === 2 && i === 1)) break;
    const a = toPx(rect, pts[i]);
    const b = toPx(rect, pts[(i + 1) % n]);
    const closing = i === n - 1;
    ctx.beginPath();
    ctx.moveTo(...a);
    ctx.lineTo(...b);
    ctx.lineWidth = 3;
    ctx.strokeStyle = validation.badEdges.has(i) ? COLORS.invalid : COLORS.draft;
    ctx.setLineDash(closing ? [6, 5] : []);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  pts.forEach((p, i) => {
    const [x, y] = toPx(rect, p);
    ctx.beginPath();
    ctx.arc(x, y, i === draft.selected ? 9 : 7, 0, Math.PI * 2);
    ctx.fillStyle = i === draft.selected ? '#ffffff' : COLORS.draft;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#0b1220';
    ctx.stroke();
    ctx.fillStyle = '#0b1220';
    ctx.font = '700 10px Inter, system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(String(i + 1), x, y + 3.5);
    ctx.textAlign = 'left';
  });
}

export { STATUS };
