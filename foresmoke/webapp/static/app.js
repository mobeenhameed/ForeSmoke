// Workspace glue: wires the sidebar, player, timeline and incident panel together.

import { api } from './api.js';
import { Annotator } from './annotator.js';
import { IncidentPanel } from './incidents.js';
import { drawDetections, drawDraft, drawFrameBackdrop, drawZone } from './overlay.js';
import { Player } from './player.js';
import { RunView } from './runview.js';
import { at, clock } from './status.js';
import { Timeline } from './timeline.js';

const $ = (id) => document.getElementById(id);
const WATCHABLE = ['chair', 'backpack', 'suitcase', 'handbag', 'bench', 'couch', 'potted_plant', 'dining_table', 'bicycle', 'umbrella'];
const DEFAULT_LABELS = ['chair', 'backpack', 'suitcase'];
const POLL_MS = 800;

const state = {
  videos: [],
  zones: [],
  runs: [],
  video: null,        // selected video metadata
  zone: null,         // selected saved zone item ({config_id, config, ...})
  run: null,          // RunView of the loaded run
  activeRunId: null,  // analysis in flight
  pollTimer: null,
  scrubbing: false,
};

// ---------------------------------------------------------------------------------------
// Feedback
let toastTimer = null;
function toast(message, kind = 'error') {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast toast-${kind}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, kind === 'error' ? 7000 : 3500);
}

const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

// ---------------------------------------------------------------------------------------
// Core widgets
const player = new Player({ video: $('video'), stage: $('stage'), canvas: $('overlay'), onTick });
const annotator = new Annotator({ player, canvas: $('overlay'), onChange: renderDrawState });
const timeline = new Timeline({ root: $('timeline'), onSeek: seekTo });
const panel = new IncidentPanel({
  statusRoot: $('zoneStatus'), activeRoot: $('activeIncident'), historyRoot: $('incidentHistory'), onSeek: seekTo,
});

function seekTo(tMs) {
  if (annotator.active) return;
  player.pause();
  player.seek(tMs);
}

function onTick(tMs) {
  $('sourceClock').textContent = player.loaded ? clock(tMs) : '--:--.---';
  $('timeNow').textContent = clock(tMs);
  $('timeTotal').textContent = clock(player.durationMs);
  if (!state.scrubbing && player.durationMs > 0) $('scrub').value = String(Math.round((tMs / player.durationMs) * 1000));
  $('playBtn').textContent = player.playing ? '❚❚' : '▶';
  $('playBtn').setAttribute('aria-label', player.playing ? 'Pause' : 'Play');
  timeline.setTime(tMs);
  if (state.run) panel.update(tMs);
}

player.renderer = (ctx, rect, tMs) => {
  if (!player.hasVideo) {
    drawFrameBackdrop(ctx, rect, state.run ? 'No source video for this run · engine geometry only' : '');
  }
  if (annotator.active) {
    drawDraft(ctx, rect, annotator.draft, annotator.validation);
    return;
  }
  const run = state.run;
  if (run) {
    const zoneStates = run.zoneIds.map((zoneId) => ({ zoneId, state: run.stateAt(zoneId, tMs) }));
    for (const { zoneId, state: zs } of zoneStates) {
      const zone = run.zoneById.get(zoneId);
      if (zone) drawZone(ctx, rect, zone, zs);
    }
    const watched = new Set([...run.zoneById.values()].flatMap((z) => z.candidate_labels));
    drawDetections(ctx, rect, run.observationAt(tMs), zoneStates, watched, $('debugToggle').checked);
  }
  const usedByRun = run && run.meta.zone_config_id && state.zone && run.meta.zone_config_id === state.zone.config_id;
  if (state.zone && player.hasVideo && !usedByRun) {
    for (const zone of state.zone.config.zones) drawZone(ctx, rect, zone, null, { dashed: true });
  }
};

// ---------------------------------------------------------------------------------------
// Videos
function videoLabel(v) {
  const kind = v.kind === 'sample' ? 'sample' : 'upload';
  return `${v.name} · ${v.duration_s.toFixed(1)} s · ${v.width}×${v.height} (${kind})`;
}

function renderVideoSelect() {
  const select = $('videoSelect');
  select.innerHTML = '';
  const none = new Option('— choose a video —', '');
  select.append(none);
  for (const v of state.videos) select.append(new Option(videoLabel(v), v.video_id));
  select.value = state.video ? state.video.video_id : '';
}

function renderVideoInfo() {
  const info = $('videoInfo');
  const v = state.video;
  info.hidden = !v;
  info.innerHTML = '';
  if (!v) return;
  const add = (k, val) => {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = val;
    info.append(dt, dd);
  };
  add('Duration', `${v.duration_s.toFixed(2)} s`);
  add('Dimensions', `${v.width} × ${v.height} px`);
  add('Frame rate', `${Number(v.fps).toFixed(2)} fps`);
  add('Frames', String(v.frame_count));
}

async function selectVideo(videoId, { keepRun = false } = {}) {
  if (annotator.active) stopDrawing();
  const video = state.videos.find((v) => v.video_id === videoId) || null;
  state.video = video;
  renderVideoSelect();
  renderVideoInfo();
  if (!keepRun && state.run && state.run.meta.video_id !== videoId) unloadRun();
  $('stageEmpty').hidden = Boolean(video) || Boolean(state.run);
  if (video) {
    await player.loadVideo(api.videoUrl(video.video_id), video);
    checkBrowserFrame(video);
  } else if (!state.run) {
    player.clear();
  }
  const zones = zonesForVideo();
  if (!state.zone || !zones.some((z) => z.config_id === state.zone.config_id)) {
    state.zone = zones.find((z) => z.source === 'user') || zones[0] || null;
  }
  renderZones();
  updateRunButton();
  player.invalidate();
}

function checkBrowserFrame(video) {
  const el = $('video');
  if (!el.videoWidth) {
    if (el.error) toast('Your browser cannot play this file. MP4 (H.264) is the most compatible format.');
    return;
  }
  const browser = el.videoWidth / el.videoHeight;
  const probed = video.width / video.height;
  if (Math.abs(browser / probed - 1) > 0.01) {
    toast(`The browser shows this video at ${el.videoWidth}×${el.videoHeight}, but analysis sees ${video.width}×${video.height}. `
      + 'Overlays may be misaligned (rotation metadata?).', 'warn');
  }
}

$('videoSelect').addEventListener('change', (e) => selectVideo(e.target.value));

$('uploadInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const bar = $('uploadProgress');
  bar.hidden = false;
  bar.firstElementChild.style.width = '0%';
  try {
    const meta = await api.uploadVideo(file, (f) => { bar.firstElementChild.style.width = `${Math.round(f * 100)}%`; });
    state.videos = await api.videos();
    toast(`Uploaded ${meta.name}: ${meta.duration_s.toFixed(1)} s, ${meta.width}×${meta.height}.`, 'ok');
    await selectVideo(meta.video_id);
  } catch (error) {
    toast(error.message);
  } finally {
    bar.hidden = true;
  }
});

// ---------------------------------------------------------------------------------------
// Zones
function zonesForVideo() {
  const v = state.video;
  if (!v) return [];
  return state.zones.filter((z) => {
    const ref = z.config.reference_frame;
    if (z.source === 'bundled') return ref.width === v.width && ref.height === v.height;
    return z.video_id === v.video_id;
  });
}

function renderZones() {
  const list = $('zoneList');
  list.innerHTML = '';
  if (!state.video) {
    list.append(Object.assign(document.createElement('p'), { className: 'muted small', textContent: 'Choose a video first.' }));
    return;
  }
  const zones = zonesForVideo();
  if (!zones.length) {
    list.append(Object.assign(document.createElement('p'), { className: 'muted small', textContent: 'No zone drawn for this video yet.' }));
  }
  for (const z of zones) {
    const item = document.createElement('label');
    item.className = `zone-item${state.zone && state.zone.config_id === z.config_id ? ' selected' : ''}`;
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'zone';
    radio.value = z.config_id;
    radio.checked = Boolean(state.zone && state.zone.config_id === z.config_id);
    radio.addEventListener('change', () => { state.zone = z; renderZones(); updateRunButton(); player.invalidate(); });
    const text = document.createElement('span');
    const zone = z.config.zones[0];
    const title = document.createElement('strong');
    title.textContent = z.name;
    const sub = document.createElement('span');
    sub.className = 'muted small';
    const bits = [`${z.config.camera_id} / ${z.config.view_id}`, `${zone.polygon.length} corners`,
      `confirm ${(zone.confirm_ms / 1000).toFixed(1)} s`, `resolve ${(zone.resolve_ms / 1000).toFixed(1)} s`];
    if (z.frame_t_ms != null) bits.push(`drawn at ${at(z.frame_t_ms)}`);
    if (z.source === 'bundled') bits.push('from configs/zones.json');
    sub.textContent = bits.join(' · ');
    text.append(title, sub);
    item.append(radio, text);
    list.append(item);
  }
}

function renderLabelChoices() {
  const root = $('labelChoices');
  root.innerHTML = '';
  for (const label of WATCHABLE) {
    const item = document.createElement('label');
    item.className = 'check';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.value = label;
    box.checked = DEFAULT_LABELS.includes(label);
    box.addEventListener('change', renderDrawState);
    item.append(box, document.createTextNode(` ${label.replace('_', ' ')}`));
    root.append(item);
  }
}

function readParams() {
  const labels = [...$('labelChoices').querySelectorAll('input:checked')].map((b) => b.value);
  const confirmMs = Math.round(Number($('confirmS').value) * 1000);
  const resolveMs = Math.round(Number($('resolveS').value) * 1000);
  const minConfidence = Number($('minConf').value);
  const errors = [];
  if (!labels.length) errors.push('Choose at least one object type to watch.');
  if (!Number.isFinite(confirmMs) || confirmMs < 1) errors.push('Confirm time must be a positive number of seconds.');
  if (!Number.isFinite(resolveMs) || resolveMs < 1) errors.push('Resolve time must be a positive number of seconds.');
  if (!Number.isFinite(minConfidence) || minConfidence < 0 || minConfidence > 1) errors.push('Minimum confidence must be between 0 and 1.');
  return {
    params: { candidate_labels: labels, confirm_ms: confirmMs, resolve_ms: resolveMs, min_confidence: minConfidence },
    errors,
  };
}

function startDrawing() {
  if (!state.video) { toast('Choose or upload a video first.'); return; }
  if (!player.hasVideo) { toast('The selected video is not loaded in the player yet.'); return; }
  if (state.run && state.run.meta.video_id !== state.video.video_id) unloadRun();
  annotator.start();
  $('drawPanel').hidden = false;
  $('drawBtn').hidden = true;
  document.body.classList.add('is-drawing');
  renderDrawState();
}

function stopDrawing() {
  annotator.stop();
  $('drawPanel').hidden = true;
  $('drawBtn').hidden = false;
  document.body.classList.remove('is-drawing');
  updateRunButton();
}

function renderDrawState() {
  if (!annotator.active) return;
  const v = annotator.validation;
  const { errors: paramErrors } = readParams();
  const name = $('zoneName').value.trim();
  const list = $('drawErrors');
  list.innerHTML = '';
  const messages = [...v.errors.map((m) => ['error', m]), ...paramErrors.map((m) => ['error', m]),
    ...v.warnings.map((m) => ['warn', m])];
  if (!name) messages.push(['error', 'Give the zone a name.']);
  if (!messages.length) messages.push(['ok', `Valid zone with ${annotator.draft.points.length} corners on a ${state.video.width}×${state.video.height} frame.`]);
  for (const [kind, text] of messages) {
    const li = document.createElement('li');
    li.className = `msg-${kind}`;
    li.textContent = text;
    list.append(li);
  }
  $('saveZoneBtn').disabled = !(v.ok && !paramErrors.length && name);
  $('undoBtn').disabled = !annotator.draft.history.length;
  $('deletePointBtn').disabled = annotator.draft.selected < 0;
  $('resetBtn').disabled = !annotator.draft.points.length;
}

async function saveZone() {
  const v = annotator.validation;
  const { params, errors } = readParams();
  if (!v.ok || errors.length) { renderDrawState(); return; }
  const button = $('saveZoneBtn');
  button.disabled = true;
  try {
    const saved = await api.saveZone({
      video_id: state.video.video_id,
      name: $('zoneName').value.trim(),
      camera_name: $('cameraName').value.trim(),
      view_name: $('viewName').value.trim(),
      polygon: annotator.polygon,
      params,
      frame_t_ms: Math.round(player.timeMs),
    });
    state.zones = await api.zones();
    state.zone = state.zones.find((z) => z.config_id === saved.config_id) || saved;
    stopDrawing();
    renderZones();
    updateRunButton();
    toast(`Zone “${saved.name}” saved (${saved.config.view_id}).`, 'ok');
  } catch (error) {
    const li = document.createElement('li');
    li.className = 'msg-error';
    li.textContent = `The engine rejected this zone: ${error.message}`;
    $('drawErrors').prepend(li);
    button.disabled = false;
  }
}

$('drawBtn').addEventListener('click', startDrawing);
$('cancelDrawBtn').addEventListener('click', stopDrawing);
$('saveZoneBtn').addEventListener('click', saveZone);
$('undoBtn').addEventListener('click', () => annotator.undo());
$('resetBtn').addEventListener('click', () => annotator.reset());
$('deletePointBtn').addEventListener('click', () => annotator.deleteSelected());
for (const id of ['zoneName', 'confirmS', 'resolveS', 'minConf']) $(id).addEventListener('input', renderDrawState);

// ---------------------------------------------------------------------------------------
// Runs: listing, loading, analysis
function runTitle(r) {
  if (r.source === 'sample') return 'Sample run (synthetic, no video)';
  if (r.video_name) return r.video_name;
  return r.run_id;
}

function renderRuns() {
  const list = $('runList');
  list.innerHTML = '';
  if (!state.runs.length) {
    list.append(Object.assign(document.createElement('li'), { className: 'muted small', textContent: 'No runs yet.' }));
    return;
  }
  for (const r of state.runs) {
    const li = document.createElement('li');
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `run-item${state.run && state.run.meta.run_id === r.run_id ? ' selected' : ''}`;
    const title = document.createElement('strong');
    title.textContent = runTitle(r);
    const badge = document.createElement('span');
    badge.className = `run-state state-${r.state}`;
    badge.textContent = r.state === 'running' ? 'analysing…' : r.state;
    const sub = document.createElement('span');
    sub.className = 'muted small';
    const bits = [];
    if (r.created_at) bits.push(fmtDate(r.created_at));
    if (r.zone_name) bits.push(r.zone_name);
    if (r.confirmed_count != null) bits.push(`${r.confirmed_count} confirmed · ${r.resolved_count} resolved`);
    if (r.source === 'external') bits.push('runs/' + r.run_id);
    sub.textContent = bits.join(' · ');
    const top = document.createElement('span');
    top.className = 'run-item-top';
    top.append(title, badge);
    b.append(top, sub);
    b.disabled = r.state === 'running';
    b.addEventListener('click', () => loadRun(r.run_id));
    li.append(b);
    list.append(li);
  }
}

function unloadRun() {
  state.run = null;
  $('loadedRun').textContent = 'No run loaded';
  timeline.clear();
  panel.clear();
  renderRuns();
  player.invalidate();
}

async function loadRun(runId) {
  if (annotator.active) stopDrawing();
  try {
    const [meta, states, events, observations, zones] = await Promise.all([
      api.run(runId), api.states(runId), api.events(runId), api.observations(runId), api.runZones(runId),
    ]);
    const view = new RunView({ meta, states, events, observations, zones });
    state.run = view;
    const hasVideo = meta.video_id && state.videos.some((v) => v.video_id === meta.video_id);
    if (hasVideo) {
      await selectVideo(meta.video_id, { keepRun: true });
    } else {
      state.video = null;
      renderVideoSelect();
      renderVideoInfo();
      renderZones();
      player.loadVirtual({ width: meta.width || 16, height: meta.height || 9, duration_s: view.durationMs / 1000 });
      $('stageEmpty').hidden = true;
    }
    timeline.render(view, Math.max(player.durationMs, view.durationMs));
    panel.load(view);
    player.seek(0);
    panel.update(0);
    const confirmed = view.incidents.length;
    const resolved = view.incidents.filter((i) => i.resolved).length;
    $('loadedRun').textContent = `${runTitle(meta)} · ${confirmed} confirmed, ${resolved} resolved`;
    renderRuns();
    updateRunButton();
  } catch (error) {
    toast(`Could not open run: ${error.message}`);
  }
}

function zoneMatchesVideo() {
  if (!state.zone || !state.video) return false;
  const ref = state.zone.config.reference_frame;
  return ref.width === state.video.width && ref.height === state.video.height;
}

function updateRunButton() {
  const button = $('runBtn');
  const busy = Boolean(state.activeRunId);
  let reason = '';
  if (busy) reason = 'An analysis is already running.';
  else if (annotator.active) reason = 'Save or cancel the zone you are drawing first.';
  else if (!state.video) reason = 'Choose a video first.';
  else if (!state.zone) reason = 'Draw and save a zone first.';
  else if (!zoneMatchesVideo()) reason = 'The selected zone was drawn on a different frame size.';
  button.disabled = Boolean(reason);
  button.title = reason || `Analyse ${state.video.name} with zone “${state.zone.name}”`;
  button.textContent = busy ? 'Analysis running…' : 'Run analysis';
}

function showRunMessage(kind, text, extra) {
  const box = $('runMessage');
  box.hidden = false;
  box.className = `run-message msg-${kind}`;
  box.innerHTML = '';
  box.append(Object.assign(document.createElement('p'), { textContent: text }));
  if (extra) box.append(extra);
}

async function startRun() {
  if (!state.video || !state.zone) return;
  $('runMessage').hidden = true;
  try {
    const meta = await api.startRun(state.video.video_id, state.zone.config_id);
    if (meta.state === 'failed') {
      showRunMessage('error', meta.error || 'Analysis could not start.');
      return;
    }
    trackRun(meta.run_id);
    state.runs = await api.runs();
    renderRuns();
  } catch (error) {
    showRunMessage('error', error.message);
  }
}

function trackRun(runId) {
  state.activeRunId = runId;
  $('runProgress').hidden = false;
  $('runProgress').querySelector('.progress span').style.width = '0%';
  $('runProgressText').textContent = 'Starting analysis (loading the detection model)…';
  updateRunButton();
  clearTimeout(state.pollTimer);
  poll();
}

async function poll() {
  const runId = state.activeRunId;
  if (!runId) return;
  let status;
  try {
    status = await api.run(runId);
  } catch (error) {
    $('runProgressText').textContent = `Waiting for server… (${error.message})`;
    state.pollTimer = setTimeout(poll, POLL_MS * 2);
    return;
  }
  const pct = Math.round(status.progress * 100);
  $('runProgress').querySelector('.progress span').style.width = `${pct}%`;
  $('runElapsed').textContent = `${Math.round(status.elapsed_s || 0)} s`;
  if (status.state === 'running') {
    $('runProgressText').textContent = status.processed_observations
      ? `Analysed ${status.processed_observations} of ~${status.expected_observations} frames (${pct}%)`
      : 'Starting analysis (loading the detection model)…';
    state.pollTimer = setTimeout(poll, POLL_MS);
    return;
  }
  state.activeRunId = null;
  $('runProgress').hidden = true;
  updateRunButton();
  state.runs = await api.runs().catch(() => state.runs);
  renderRuns();
  if (status.state === 'done') {
    const events = await api.events(runId).catch(() => []);
    const confirmed = events.filter((e) => e.event_type === 'incident_confirmed').length;
    const resolved = events.filter((e) => e.event_type === 'incident_resolved').length;
    showRunMessage('ok', `Analysis complete in ${Math.round(status.elapsed_s || 0)} s of processing: `
      + `${confirmed} incident${confirmed === 1 ? '' : 's'} confirmed, ${resolved} resolved. Results are open for review.`);
    await loadRun(runId);
  } else {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Show analysis log';
    const pre = document.createElement('pre');
    pre.textContent = await api.runLog(runId).catch(() => '(log unavailable)');
    details.append(summary, pre);
    if (status.state === 'cancelled') {
      showRunMessage('warn', 'Analysis cancelled. Any partial output stays in its own run folder.', details);
    } else {
      showRunMessage('error', status.error || 'Analysis failed.', details);
    }
  }
}

$('runBtn').addEventListener('click', startRun);
$('cancelRunBtn').addEventListener('click', async () => {
  if (!state.activeRunId) return;
  $('cancelRunBtn').disabled = true;
  $('runProgressText').textContent = 'Cancelling…';
  try { await api.cancelRun(state.activeRunId); } catch (error) { toast(error.message); }
  $('cancelRunBtn').disabled = false;
});

// ---------------------------------------------------------------------------------------
// Transport
$('playBtn').addEventListener('click', () => { if (!annotator.active) player.toggle(); });
$('backBtn').addEventListener('click', () => player.step(-1));
$('fwdBtn').addEventListener('click', () => player.step(1));
$('scrub').addEventListener('pointerdown', () => { state.scrubbing = true; });
$('scrub').addEventListener('pointerup', () => { state.scrubbing = false; });
$('scrub').addEventListener('input', (e) => { player.pause(); player.seek((Number(e.target.value) / 1000) * player.durationMs); });
$('debugToggle').addEventListener('change', () => player.invalidate());
window.addEventListener('keydown', (e) => {
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.tagName === 'BUTTON')) return;
  if (e.key === ' ') { e.preventDefault(); if (!annotator.active) player.toggle(); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); player.step(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); player.step(1); }
  if (e.key === 'Escape' && annotator.active) stopDrawing();
});
$('video').addEventListener('play', () => { if (annotator.active) player.pause(); });

// ---------------------------------------------------------------------------------------
// Boot
async function boot() {
  renderLabelChoices();
  timeline.clear();
  panel.clear();
  try {
    [state.videos, state.zones, state.runs] = await Promise.all([api.videos(), api.zones(), api.runs()]);
  } catch (error) {
    toast(`Could not reach the ForeSmoke server: ${error.message}`);
    return;
  }
  renderVideoSelect();
  renderZones();
  renderRuns();
  const running = state.runs.find((r) => r.state === 'running');
  if (running) trackRun(running.run_id);
  // Open the most recent finished run so the viewer works before any new analysis.
  const finished = state.runs.find((r) => r.state === 'done' && r.files && r.files.includes('states.jsonl'));
  if (finished) await loadRun(finished.run_id);
  else if (state.videos.length) await selectVideo(state.videos[0].video_id);
  updateRunButton();
}

boot();
