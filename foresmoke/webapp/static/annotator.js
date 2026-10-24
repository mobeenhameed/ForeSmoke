// Polygon annotation on the paused frame: click to add, drag to move, right-click or
// Delete to remove, Ctrl/Cmd+Z to undo. Points are stored normalized to the TRUE frame size.

import { round6, validatePolygon } from './geometry.js';

const HIT_RADIUS = 11;

export class Annotator {
  constructor({ player, canvas, onChange }) {
    this.player = player;
    this.canvas = canvas;
    this.onChange = onChange;
    this.active = false;
    this.draft = null;
    this.drag = null;
    canvas.addEventListener('pointerdown', (e) => this._down(e));
    canvas.addEventListener('pointermove', (e) => this._move(e));
    canvas.addEventListener('pointerup', (e) => this._up(e));
    canvas.addEventListener('pointercancel', (e) => this._up(e));
    canvas.addEventListener('contextmenu', (e) => this._context(e));
    window.addEventListener('keydown', (e) => this._key(e));
  }

  start(points = []) {
    this.player.pause();
    this.active = true;
    this.draft = { points: points.map((p) => [...p]), history: [], selected: -1 };
    this.canvas.classList.add('drawing');
    this._changed();
  }

  stop() {
    this.active = false;
    this.draft = null;
    this.drag = null;
    this.canvas.classList.remove('drawing');
    this._changed();
  }

  get validation() { return validatePolygon(this.draft ? this.draft.points : []); }

  /** Normalized, 6-decimal points ready for the ZoneConfig polygon (no closing vertex). */
  get polygon() { return this.draft ? this.draft.points.map(([x, y]) => [round6(x), round6(y)]) : []; }

  undo() {
    if (!this.draft || !this.draft.history.length) return;
    this.draft.points = this.draft.history.pop();
    this.draft.selected = -1;
    this._changed();
  }

  reset() {
    if (!this.draft || !this.draft.points.length) return;
    this._remember();
    this.draft.points = [];
    this.draft.selected = -1;
    this._changed();
  }

  deleteSelected() {
    if (!this.draft || this.draft.selected < 0) return;
    this._remember();
    this.draft.points.splice(this.draft.selected, 1);
    this.draft.selected = -1;
    this._changed();
  }

  _remember() {
    this.draft.history.push(this.draft.points.map((p) => [...p]));
    if (this.draft.history.length > 200) this.draft.history.shift();
  }

  _changed() {
    this.player.invalidate();
    if (this.onChange) this.onChange();
  }

  _hit(e) {
    const bounds = this.canvas.getBoundingClientRect();
    const mx = e.clientX - bounds.left;
    const my = e.clientY - bounds.top;
    let best = -1;
    let bestDist = HIT_RADIUS;
    this.draft.points.forEach((p, i) => {
      const [x, y] = this.player.toStage(p);
      const d = Math.hypot(x - mx, y - my);
      if (d <= bestDist) { best = i; bestDist = d; }
    });
    return best;
  }

  _down(e) {
    if (!this.active || e.button !== 0) return;
    e.preventDefault();
    const hit = this._hit(e);
    if (hit >= 0) {
      this._remember();
      this.draft.selected = hit;
      this.drag = { index: hit, moved: false, pointer: e.pointerId };
      this.canvas.setPointerCapture(e.pointerId);
      this._changed();
      return;
    }
    const point = this.player.toNormalized(e.clientX, e.clientY);
    if (!point) return;                               // click landed in the letterbox
    this._remember();
    this.draft.points.push(point);
    this.draft.selected = this.draft.points.length - 1;
    this._changed();
  }

  _move(e) {
    if (!this.active) return;
    if (!this.drag) {
      this.canvas.style.cursor = this._hit(e) >= 0 ? 'grab' : 'crosshair';
      return;
    }
    const point = this.player.toNormalized(e.clientX, e.clientY, { clamp: true });
    this.draft.points[this.drag.index] = point;
    this.drag.moved = true;
    this.canvas.style.cursor = 'grabbing';
    this._changed();
  }

  _up(e) {
    if (!this.drag) return;
    if (!this.drag.moved) this.draft.history.pop();   // a click on a handle only selects it
    try { this.canvas.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    this.drag = null;
    this._changed();
  }

  _context(e) {
    if (!this.active) return;
    e.preventDefault();
    const hit = this._hit(e);
    if (hit < 0) return;
    this.draft.selected = hit;
    this.deleteSelected();
  }

  _key(e) {
    if (!this.active) return;
    const target = e.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); this.undo(); return; }
    if ((e.key === 'Delete' || e.key === 'Backspace') && this.draft.selected >= 0) {
      e.preventDefault();
      this.deleteSelected();
    }
  }
}
