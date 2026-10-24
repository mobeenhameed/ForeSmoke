// Video player with a canvas overlay. Time is always source-video time in milliseconds.
// Runs without a video (the synthetic sample) use a virtual clock over the same timeline.

export class Player {
  constructor({ video, stage, canvas, onTick }) {
    this.video = video;
    this.stage = stage;
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onTick = onTick;
    this.frame = null;       // { width, height, duration_ms, fps } of the true source frame
    this.hasVideo = false;
    this.virtualMs = 0;
    this.virtualPlaying = false;
    this.lastRaf = null;
    this.dirty = true;
    this.renderer = null;    // (ctx, rect, timeMs) => void
    this._resize = () => { this.resizeCanvas(); this.dirty = true; };
    new ResizeObserver(this._resize).observe(stage);
    for (const name of ['seeked', 'timeupdate', 'loadeddata', 'pause', 'play']) {
      video.addEventListener(name, () => { this.dirty = true; });
    }
    video.addEventListener('ended', () => { this.dirty = true; });
    requestAnimationFrame((t) => this._loop(t));
  }

  /** Load a real source video. meta carries probed width/height/fps/duration_s. */
  loadVideo(url, meta) {
    this.pause();
    this.hasVideo = true;
    this.frame = { width: meta.width, height: meta.height, fps: meta.fps, duration_ms: meta.duration_s * 1000 };
    this.virtualMs = 0;
    if (this.video.dataset.src !== url) {
      this.video.dataset.src = url;
      this.video.src = url;
      this.video.load();
    }
    this.video.hidden = false;
    this.dirty = true;
    return new Promise((resolve) => {
      if (this.video.readyState >= 2) { resolve(); return; }
      const done = () => { this.video.removeEventListener('loadeddata', done); resolve(); };
      this.video.addEventListener('loadeddata', done);
      this.video.addEventListener('error', done, { once: true });
    });
  }

  /** Timeline-only playback for runs without a source video. */
  loadVirtual({ width, height, duration_s }) {
    this.pause();
    this.hasVideo = false;
    this.video.removeAttribute('src');
    delete this.video.dataset.src;
    this.video.load();
    this.video.hidden = true;
    this.frame = { width, height, fps: null, duration_ms: duration_s * 1000 };
    this.virtualMs = 0;
    this.dirty = true;
  }

  clear() {
    this.loadVirtual({ width: 16, height: 9, duration_s: 0 });
    this.frame = null;
  }

  get loaded() { return Boolean(this.frame); }
  get durationMs() {
    if (!this.frame) return 0;
    if (this.hasVideo && Number.isFinite(this.video.duration)) return this.video.duration * 1000;
    return this.frame.duration_ms;
  }
  get playing() { return this.hasVideo ? !this.video.paused && !this.video.ended : this.virtualPlaying; }
  get timeMs() { return this.hasVideo ? this.video.currentTime * 1000 : this.virtualMs; }

  /** Seek to a source t_ms. Lands mid-frame so the displayed frame is the one with that t_ms. */
  seek(tMs) {
    const clamped = Math.max(0, Math.min(tMs, this.durationMs || tMs));
    if (this.hasVideo) {
      const half = this.frame && this.frame.fps ? 500 / this.frame.fps : 0;
      const target = Math.min(clamped + half, Math.max(0, this.durationMs - 1));
      this.video.currentTime = target / 1000;
    } else {
      this.virtualMs = clamped;
    }
    this.dirty = true;
  }

  play() {
    if (!this.frame) return;
    if (this.hasVideo) {
      if (this.video.ended) this.video.currentTime = 0;
      this.video.play().catch(() => {});
    } else {
      if (this.virtualMs >= this.durationMs) this.virtualMs = 0;
      this.virtualPlaying = true;
    }
    this.dirty = true;
  }

  pause() {
    if (this.hasVideo) this.video.pause();
    this.virtualPlaying = false;
    this.dirty = true;
  }

  toggle() { if (this.playing) this.pause(); else this.play(); }

  /** Step one source frame (or one 200 ms analysis tick when there is no video). */
  step(direction) {
    this.pause();
    const stepMs = this.frame && this.frame.fps ? 1000 / this.frame.fps : 200;
    const base = this.hasVideo && this.frame.fps
      ? Math.floor(this.timeMs / stepMs + 1e-6) * stepMs
      : this.timeMs;
    this.seek(base + direction * stepMs);
  }

  resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const { clientWidth: w, clientHeight: h } = this.stage;
    this.canvas.width = Math.max(1, Math.round(w * dpr));
    this.canvas.height = Math.max(1, Math.round(h * dpr));
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /** Where the true frame is drawn inside the stage (object-fit: contain letterboxing). */
  frameRect() {
    const { clientWidth: sw, clientHeight: sh } = this.stage;
    if (!this.frame) return { x: 0, y: 0, w: sw, h: sh };
    const scale = Math.min(sw / this.frame.width, sh / this.frame.height);
    const w = this.frame.width * scale;
    const h = this.frame.height * scale;
    return { x: (sw - w) / 2, y: (sh - h) / 2, w, h, scale };
  }

  /** Stage CSS pixel -> true frame pixel -> normalized [0,1]. Returns null outside the frame. */
  toNormalized(clientX, clientY, { clamp = false } = {}) {
    const bounds = this.stage.getBoundingClientRect();
    const r = this.frameRect();
    let px = ((clientX - bounds.left - r.x) / r.w) * this.frame.width;   // true-frame pixels
    let py = ((clientY - bounds.top - r.y) / r.h) * this.frame.height;
    if (clamp) {
      px = Math.max(0, Math.min(this.frame.width, px));
      py = Math.max(0, Math.min(this.frame.height, py));
    } else if (px < 0 || py < 0 || px > this.frame.width || py > this.frame.height) {
      return null;
    }
    return [px / this.frame.width, py / this.frame.height];
  }

  toStage([nx, ny]) {
    const r = this.frameRect();
    return [r.x + nx * r.w, r.y + ny * r.h];
  }

  invalidate() { this.dirty = true; }

  _loop(now) {
    if (this.virtualPlaying) {
      const dt = this.lastRaf == null ? 0 : now - this.lastRaf;
      this.virtualMs = Math.min(this.virtualMs + dt, this.durationMs);
      if (this.virtualMs >= this.durationMs) this.virtualPlaying = false;
      this.dirty = true;
    }
    if (this.hasVideo && this.playing) this.dirty = true;
    this.lastRaf = now;
    if (this.dirty) {
      this.dirty = false;
      const { clientWidth: w, clientHeight: h } = this.stage;
      this.ctx.clearRect(0, 0, w, h);
      if (this.renderer && this.frame) this.renderer(this.ctx, this.frameRect(), this.timeMs);
      if (this.onTick) this.onTick(this.timeMs);
    }
    requestAnimationFrame((t) => this._loop(t));
  }
}
