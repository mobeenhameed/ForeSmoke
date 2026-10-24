// Workspace glue: wires the sidebar, player, timeline and incident panel together.

import { api } from './api.js';
import { Annotator } from './annotator.js';
import { IncidentPanel } from './incidents.js';
import { drawDetections, drawDraft, drawFrameBackdrop, drawProposal, drawZone } from './overlay.js';
import { Player } from './player.js';
import { RunView } from './runview.js';
import { at, clock, statusOf } from './status.js';
import { Timeline } from './timeline.js';

const $ = (id) => document.getElementById(id);
const WATCHABLE = ['chair', 'backpack', 'suitcase', 'handbag', 'bench', 'couch', 'potted_plant', 'dining_table', 'bicycle', 'umbrella'];
const DEFAULT_LABELS = ['chair', 'backpack', 'suitcase'];
const HIDDEN_VIDEO_NAMES = new Set(['chair.mp4', 'chair_2.mp4']);
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
  libraryFilter: 'all',
  page: 'overview',
  previousPage: 'overview',
  syncingVideos: false,
  proposal: null,
  proposalPollTimer: null,
  proposalGeneration: 0,
  draftProvenance: null,
  liveEpochMs: Date.now(),
  liveTimer: null,
};

const PAGES = { overview: 'overviewPage', live: 'livePage', videos: 'videosPage', reviews: 'libraryPanel', workspace: 'workspacePage' };

function showPage(page) {
  if (!PAGES[page]) return;
  if (page !== 'workspace' && !$('setupPanel').hidden) closeSetup();
  state.page = page;
  for (const [name, id] of Object.entries(PAGES)) $(id).hidden = name !== page;
  for (const button of document.querySelectorAll('[data-app-nav]')) {
    const selected = button.dataset.appNav === page;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  }
  setLiveActive(page === 'live');
  window.scrollTo(0, 0);
}

function openSetup() {
  showPage('workspace');
  $('setupPanel').hidden = false;
  $('workspacePage').classList.add('setup-open');
  $('newAnalysisBtn').setAttribute('aria-expanded', 'true');
  $('videoSelect').focus();
}

function closeSetup() {
  if (annotator.active) stopDrawing();
  $('setupPanel').hidden = true;
  $('workspacePage').classList.remove('setup-open');
  $('newAnalysisBtn').setAttribute('aria-expanded', 'false');
}

function openLibrary() {
  if (state.page !== 'reviews') state.previousPage = state.page;
  showPage('reviews');
  $('runSearch').focus();
}

function closeLibrary() {
  if (state.page === 'reviews') showPage(state.previousPage);
}

$('newAnalysisBtn').setAttribute('aria-controls', 'setupPanel');
$('newAnalysisBtn').setAttribute('aria-expanded', 'false');
$('newAnalysisBtn').addEventListener('click', () => {
  if ($('setupPanel').hidden) openSetup(); else closeSetup();
});
$('setupCloseBtn').addEventListener('click', closeSetup);
for (const button of document.querySelectorAll('[data-app-nav]')) {
  button.addEventListener('click', () => {
    if (button.dataset.appNav === 'reviews') openLibrary();
    else showPage(button.dataset.appNav);
  });
}
$('libraryCloseBtn').addEventListener('click', closeLibrary);
$('globalRunStatus').addEventListener('click', openSetup);
$('overviewNewBtn').addEventListener('click', openSetup);
$('viewAllReviewsBtn').addEventListener('click', openLibrary);
$('viewAllVideosBtn').addEventListener('click', () => showPage('videos'));
$('videoUploadBtn').addEventListener('click', () => { openSetup(); $('uploadInput').click(); });
$('runSearch').addEventListener('input', renderRuns);
for (const button of document.querySelectorAll('[data-run-filter]')) {
  button.addEventListener('click', () => {
    state.libraryFilter = button.dataset.runFilter;
    for (const option of document.querySelectorAll('[data-run-filter]')) {
      const active = option === button;
      option.classList.toggle('selected', active);
      option.setAttribute('aria-pressed', String(active));
    }
    renderRuns();
  });
}

function renderReviewSummary() {
  const run = state.run;
  $('jumpConfirmBtn').hidden = !run?.incidents.length;
  $('jumpResolveBtn').hidden = !run?.incidents.some((i) => i.resolved);
  if (!run) {
    $('reviewEyebrow').textContent = state.video?.name || 'FOOTAGE REVIEW';
    $('reviewTitle').textContent = state.video ? 'Ready to analyze' : 'Review an exit route';
    $('reviewSummary').textContent = state.video
      ? 'Select or draw the access zone, then run analysis to review findings.'
      : 'Choose a recording, mark the access area, and analyze the footage.';
    return;
  }
  const incidents = run.incidents;
  const resolved = incidents.filter((i) => i.resolved).length;
  const recorded = Boolean(run.meta.video_id);
  $('reviewEyebrow').textContent = run.meta.video_name || (recorded ? 'RECORDED FOOTAGE' : 'SYNTHETIC SAMPLE');
  if (!incidents.length) {
    $('reviewTitle').textContent = 'No confirmed obstructions';
    $('reviewSummary').textContent = 'No watched object met the confirmation rule in the marked access zone.';
    return;
  }
  $('reviewTitle').textContent = `${incidents.length} confirmed obstruction${incidents.length === 1 ? '' : 's'}`;
  const first = incidents[0];
  const object = first.confirmed.evidence.objects?.[0]?.label || 'watched object';
  $('reviewSummary').textContent = `${object[0].toUpperCase() + object.slice(1)} detected in the marked zone at ${at(first.confirmed.t_ms)}. ${resolved ? `${resolved} finding${resolved === 1 ? '' : 's'} later resolved after observed clearance.` : 'No observed resolution in this recording.'}`;
}

$('jumpConfirmBtn').addEventListener('click', () => {
  const incident = state.run?.incidents[0];
  if (incident) seekTo(incident.confirmed.evidence.t_ms);
});
$('jumpResolveBtn').addEventListener('click', () => {
  const incident = state.run?.incidents.find((i) => i.resolved);
  if (incident) seekTo(incident.resolved.evidence.t_ms);
});
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
  const proposed = selectedProposalCandidate();
  if (proposed) drawProposal(ctx, rect, proposed);
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
  const kind = v.kind === 'upload' ? 'upload' : 'repository';
  return `${v.name} · ${v.duration_s.toFixed(1)} s · ${v.width}×${v.height} (${kind})`;
}

function renderVideoSelect() {
  const select = $('videoSelect');
  select.innerHTML = '';
  const none = new Option('— choose a video —', '');
  select.append(none);
  for (const v of catalogVideos()) select.append(new Option(videoLabel(v), v.video_id));
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
  if (!state.video || state.video.video_id !== videoId) clearProposal({ cancel: true });
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
  renderReviewSummary();
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
    renderVideoCatalog();
    renderOverview();
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

function startDrawing(points = [], provenance = null) {
  if (!state.video) { toast('Choose or upload a video first.'); return; }
  if (!player.hasVideo) { toast('The selected video is not loaded in the player yet.'); return; }
  if (state.run && state.run.meta.video_id !== state.video.video_id) unloadRun();
  state.draftProvenance = provenance;
  annotator.start(points);
  $('drawPanel').hidden = false;
  $('drawBtn').hidden = true;
  document.body.classList.add('is-drawing');
  renderDrawState();
}

function stopDrawing() {
  annotator.stop();
  state.draftProvenance = null;
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
  if ($('zoneType').value === 'extinguisher_access' && !state.draftProvenance?.asset_bbox) {
    messages.push(['error', 'An extinguisher zone requires an AI proposal with a visible asset box.']);
  }
  if (!messages.length) messages.push(['ok', `Valid zone with ${annotator.draft.points.length} corners on a ${state.video.width}×${state.video.height} frame.`]);
  for (const [kind, text] of messages) {
    const li = document.createElement('li');
    li.className = `msg-${kind}`;
    li.textContent = text;
    list.append(li);
  }
  const assetReady = $('zoneType').value !== 'extinguisher_access' || Boolean(state.draftProvenance?.asset_bbox);
  $('saveZoneBtn').disabled = !(v.ok && !paramErrors.length && name && assetReady);
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
    const body = {
      video_id: state.video.video_id,
      name: $('zoneName').value.trim(),
      camera_name: $('cameraName').value.trim(),
      view_name: $('viewName').value.trim(),
      polygon: annotator.polygon,
      params,
      frame_t_ms: Math.round(player.timeMs),
      zone_type: $('zoneType').value,
      asset_name: $('zoneType').value === 'extinguisher_access' ? $('assetName').value.trim() : null,
      asset_bbox: state.draftProvenance?.asset_bbox || null,
    };
    const saved = state.draftProvenance
      ? await api.acceptZoneProposal(state.draftProvenance.job_id, {
        candidate_key: state.draftProvenance.candidate_key,
        name: body.name,
        camera_name: body.camera_name,
        view_name: body.view_name,
        polygon: body.polygon,
        params: body.params,
        asset_name: body.asset_name,
      })
      : await api.saveZone(body);
    state.zones = await api.zones();
    state.zone = state.zones.find((z) => z.config_id === saved.config_id) || saved;
    stopDrawing();
    clearProposal();
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

$('drawBtn').addEventListener('click', () => { clearProposal({ cancel: true }); startDrawing(); });
$('cancelDrawBtn').addEventListener('click', stopDrawing);
$('saveZoneBtn').addEventListener('click', saveZone);
$('undoBtn').addEventListener('click', () => annotator.undo());
$('resetBtn').addEventListener('click', () => annotator.reset());
$('deletePointBtn').addEventListener('click', () => annotator.deleteSelected());
for (const id of ['zoneName', 'confirmS', 'resolveS', 'minConf', 'assetName']) $(id).addEventListener('input', renderDrawState);
$('zoneType').addEventListener('change', () => {
  $('assetNameField').hidden = $('zoneType').value !== 'extinguisher_access';
  renderDrawState();
});

// ---------------------------------------------------------------------------------------
// AI-assisted access-area proposals
function selectedProposalCandidate() {
  const proposal = state.proposal;
  if (!proposal?.result || !proposal.selectedKey) return null;
  return proposal.result.candidates.find((item) => item.candidate_key === proposal.selectedKey) || null;
}

function clearProposal({ cancel = false } = {}) {
  const old = state.proposal;
  state.proposalGeneration += 1;
  clearTimeout(state.proposalPollTimer);
  state.proposalPollTimer = null;
  state.proposal = null;
  state.draftProvenance = null;
  $('proposalPanel').hidden = true;
  $('proposalCandidates').innerHTML = '';
  $('proposalFrame').hidden = true;
  $('proposalFrame').removeAttribute('src');
  $('suggestBtn').disabled = false;
  $('editProposalBtn').hidden = true;
  $('cancelProposalBtn').hidden = true;
  if (cancel && old?.jobId && ['pending', 'running'].includes(old.status)) {
    api.cancelZoneProposal(old.jobId).catch(() => {});
  }
  player.invalidate();
}

function renderProposal() {
  const proposal = state.proposal;
  const panel = $('proposalPanel');
  panel.hidden = !proposal;
  if (!proposal) return;
  $('cancelProposalBtn').hidden = !['pending', 'running'].includes(proposal.status);
  $('editProposalBtn').hidden = !selectedProposalCandidate()?.valid;
  const root = $('proposalCandidates');
  root.innerHTML = '';
  if (proposal.status === 'pending') $('proposalStatus').textContent = 'Suggestion queued…';
  else if (proposal.status === 'running') $('proposalStatus').textContent = 'Kiro is inspecting the frozen frame…';
  else if (proposal.status === 'failed') {
    $('proposalStatus').textContent = `Suggestion failed: ${proposal.result?.error?.message || 'unknown error'}. Manual drawing remains available.`;
  } else if (proposal.status === 'cancelled') {
    $('proposalStatus').textContent = 'Suggestion cancelled. Manual drawing remains available.';
  } else if (proposal.result?.scene_status === 'no_candidate') {
    $('proposalStatus').textContent = 'No matching exit or extinguisher was identified on this frame.';
  } else if (proposal.result?.scene_status === 'insufficient_visibility') {
    $('proposalStatus').textContent = 'The access area is not sufficiently visible. Choose a clearer frame or draw manually.';
  } else {
    const limitation = proposal.result?.scene_limitations?.[0];
    $('proposalStatus').textContent = `Review every suggestion before saving.${limitation ? ` ${limitation}` : ''}`;
  }
  for (const candidate of proposal.result?.candidates || []) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `proposal-candidate${candidate.candidate_key === proposal.selectedKey ? ' selected' : ''}${candidate.valid ? '' : ' invalid'}`;
    const title = document.createElement('strong');
    title.textContent = `${candidate.label} · ${candidate.zone_type?.replace('_', ' ') || 'invalid'}`;
    const detail = document.createElement('small');
    detail.textContent = candidate.valid
      ? `${candidate.visibility.replace('_', ' ')} · ${candidate.evidence}`
      : (candidate.validation_errors[0] || 'No usable floor polygon');
    button.append(title, detail);
    button.addEventListener('click', () => {
      if (!state.proposal) return;
      state.proposal.selectedKey = candidate.candidate_key;
      renderProposal();
      player.invalidate();
    });
    root.append(button);
  }
  player.invalidate();
}

async function suggestAccessAreas() {
  if (!state.video || !player.hasVideo) { toast('Choose a video first.'); return; }
  if (annotator.active && annotator.polygon.length) {
    toast('Save or cancel your manual draft before requesting a suggestion.');
    return;
  }
  if (annotator.active) stopDrawing();
  clearProposal({ cancel: true });
  player.pause();
  const generation = state.proposalGeneration;
  const videoId = state.video.video_id;
  const requestedAt = Math.round(player.timeMs);
  $('suggestBtn').disabled = true;
  try {
    const created = await api.suggestZones({
      video_id: videoId,
      frame_t_ms: requestedAt,
      requested_zone_type: $('proposalTarget').value,
    });
    if (generation !== state.proposalGeneration || state.video?.video_id !== videoId) return;
    state.proposal = {
      jobId: created.job_id,
      videoId,
      requestedAt,
      status: created.status,
      selectedKey: null,
      result: created,
      generation,
    };
    renderProposal();
    pollZoneProposal();
  } catch (error) {
    $('suggestBtn').disabled = false;
    toast(`Could not request a suggestion: ${error.message}`);
  }
}

async function pollZoneProposal() {
  const proposal = state.proposal;
  if (!proposal || !['pending', 'running'].includes(proposal.status)) return;
  try {
    const result = await api.zoneProposal(proposal.jobId);
    if (
      !state.proposal
      || state.proposal.generation !== proposal.generation
      || state.video?.video_id !== proposal.videoId
    ) return;
    state.proposal.status = result.status;
    state.proposal.result = result;
    if (result.status === 'completed') {
      state.proposal.selectedKey = result.candidates.find(
        (item) => item.valid && item.access_polygon && item.visibility === 'adequate',
      )?.candidate_key
        || result.candidates.find((item) => item.valid && item.access_polygon)?.candidate_key
        || result.candidates[0]?.candidate_key || null;
      const exact = result.source_frame?.source_timestamp_ms;
      if (Number.isFinite(exact)) player.seek(exact);
      $('proposalFrame').src = `${api.zoneProposalFrameUrl(proposal.jobId)}?v=${encodeURIComponent(result.source_frame?.submitted_image_sha256 || '')}`;
      $('proposalFrame').hidden = false;
      $('suggestBtn').disabled = false;
    } else if (['failed', 'cancelled'].includes(result.status)) {
      $('suggestBtn').disabled = false;
    }
    renderProposal();
    if (result.status === 'completed' && selectedProposalCandidate()?.valid && !annotator.active) {
      editSelectedProposal();
      toast('Best suggestion loaded. Review it, adjust if needed, then save the zone.', 'ok');
    }
    if (['pending', 'running'].includes(result.status)) {
      state.proposalPollTimer = setTimeout(pollZoneProposal, POLL_MS);
    }
  } catch (error) {
    if (state.proposal?.jobId === proposal.jobId) {
      $('proposalStatus').textContent = `Waiting for proposal service… ${error.message}`;
      state.proposalPollTimer = setTimeout(pollZoneProposal, POLL_MS * 2);
    }
  }
}

function editSelectedProposal() {
  const candidate = selectedProposalCandidate();
  const proposal = state.proposal;
  if (!candidate?.valid || !candidate.access_polygon || !proposal) return;
  $('zoneType').value = candidate.zone_type;
  $('assetNameField').hidden = candidate.zone_type !== 'extinguisher_access';
  $('zoneName').value = candidate.label;
  if (candidate.zone_type === 'extinguisher_access') $('assetName').value = candidate.label;
  startDrawing(candidate.access_polygon, {
    job_id: proposal.jobId,
    candidate_key: candidate.candidate_key,
    proposed_polygon: candidate.access_polygon,
    asset_bbox: candidate.asset_bbox,
  });
}

$('suggestBtn').addEventListener('click', suggestAccessAreas);
$('editProposalBtn').addEventListener('click', editSelectedProposal);
$('cancelProposalBtn').addEventListener('click', async () => {
  if (!state.proposal?.jobId) return;
  try {
    const result = await api.cancelZoneProposal(state.proposal.jobId);
    if (state.proposal) {
      state.proposal.status = result.status;
      state.proposal.result = result;
      $('suggestBtn').disabled = false;
      renderProposal();
    }
  } catch (error) {
    toast(error.message);
  }
});

// ---------------------------------------------------------------------------------------
// Runs: listing, loading, analysis
function runTitle(r) {
  if (r.source === 'sample') return 'Sample run (synthetic, no video)';
  if (r.video_name) return r.video_name;
  return r.run_id;
}

function latestReviewsByVideo() {
  const latest = new Map();
  const completed = visibleRuns().filter((r) => r.state === 'done' && r.video_id)
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  for (const run of completed) if (!latest.has(run.video_id)) latest.set(run.video_id, run);
  return latest;
}

function catalogVideos() {
  const remoteRootNames = new Set(state.videos.filter((v) => v.kind === 'github' && !v.repo_path?.includes('/'))
    .map((v) => v.repo_path));
  return state.videos.filter(
    (v) => !HIDDEN_VIDEO_NAMES.has(v.name.toLowerCase())
      && (v.kind !== 'sample' || !remoteRootNames.has(v.name)),
  );
}

function visibleRuns() {
  const hiddenIds = new Set(
    state.videos.filter((video) => HIDDEN_VIDEO_NAMES.has(video.name.toLowerCase()))
      .map((video) => video.video_id),
  );
  return state.runs.filter((run) => !hiddenIds.has(run.video_id));
}

function liveTimestamp(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function updateLiveWall() {
  if (state.page !== 'live') return;
  const now = Date.now();
  const timestamp = liveTimestamp(new Date(now));
  for (const label of document.querySelectorAll('[data-live-timestamp]')) label.textContent = timestamp;
  const elapsedS = Math.max(0, (now - state.liveEpochMs) / 1000);
  for (const video of document.querySelectorAll('#liveGrid video')) {
    if (!Number.isFinite(video.duration) || video.duration <= 0) continue;
    const target = elapsedS % video.duration;
    if (Math.abs(video.currentTime - target) > 0.75) video.currentTime = target;
    if (video.paused) video.play().catch(() => {});
    renderLiveAnalysis(video, target * 1000);
  }
}

function setLiveActive(active) {
  clearInterval(state.liveTimer);
  state.liveTimer = null;
  if (!active) {
    for (const video of document.querySelectorAll('#liveGrid video')) video.pause();
    return;
  }
  updateLiveWall();
  state.liveTimer = setInterval(updateLiveWall, 500);
}

function renderLiveAnalysis(video, tMs) {
  const canvas = video.parentElement.querySelector('canvas');
  const badge = video.closest('.live-camera')?.querySelector('.live-hazard');
  const view = video.liveRunView;
  if (!canvas || !badge || !view) return;
  const dpr = window.devicePixelRatio || 1;
  const width = video.parentElement.clientWidth;
  const height = video.parentElement.clientHeight;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const sourceWidth = video.videoWidth || view.meta.width || 16;
  const sourceHeight = video.videoHeight || view.meta.height || 9;
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const rect = {
    x: (width - sourceWidth * scale) / 2,
    y: (height - sourceHeight * scale) / 2,
    w: sourceWidth * scale,
    h: sourceHeight * scale,
  };
  const zoneStates = view.zoneIds.map((zoneId) => ({ zoneId, state: view.stateAt(zoneId, tMs) }));
  for (const { zoneId, state: zoneState } of zoneStates) {
    const zone = view.zoneById.get(zoneId);
    const status = statusOf(zoneState);
    const colorKey = status?.key === 'confirmed'
      ? 'confirmed'
      : (status?.key === 'clear' || status?.key === 'resolved' ? 'clear' : 'possible');
    if (zone) drawZone(ctx, rect, zone, zoneState, { colorKey, showLabel: false });
  }
  const priority = { confirmed: 5, possible: 4, unknown: 3, resolved: 2, clear: 1 };
  const current = zoneStates.map(({ state: zoneState }) => statusOf(zoneState)).filter(Boolean)
    .sort((a, b) => priority[b.key] - priority[a.key])[0];
  badge.className = `live-hazard live-hazard-${current?.key || 'unknown'}`;
  badge.textContent = current ? `${current.icon} ${current.label}` : '? Awaiting analyzed frame';
}

async function attachLiveAnalysis(video, run, badge) {
  if (!run) {
    badge.className = 'live-hazard live-hazard-unavailable';
    badge.textContent = 'Not analyzed';
    return;
  }
  try {
    const [meta, states, events, observations, zones] = await Promise.all([
      api.run(run.run_id), api.states(run.run_id), api.events(run.run_id).catch(() => []),
      api.observations(run.run_id).catch(() => []), api.runZones(run.run_id).catch(() => null),
    ]);
    if (!video.isConnected) return;
    video.liveRunView = new RunView({ meta, states, events, observations, zones });
    updateLiveWall();
  } catch (_) {
    badge.className = 'live-hazard live-hazard-unavailable';
    badge.textContent = 'Analysis unavailable';
  }
}

function renderLiveGrid() {
  const root = $('liveGrid');
  root.innerHTML = '';
  const latest = latestReviewsByVideo();
  const cameraFiles = catalogVideos()
    .filter((video) => /^cam[1-4]\.[^.]+$/i.test(video.name))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  const videos = cameraFiles.length === 4 ? cameraFiles : catalogVideos().slice(0, 4);
  for (let index = 0; index < 4; index += 1) {
    const source = videos[index];
    const card = document.createElement('article');
    card.className = `live-camera${source ? '' : ' offline'}`;
    const head = document.createElement('header');
    head.className = 'live-camera-head';
    const camera = document.createElement('span');
    camera.className = 'live-camera-label';
    camera.textContent = `CAM ${index + 1}`;
    const hazard = document.createElement('span');
    hazard.className = 'live-hazard live-hazard-unavailable';
    const latestRun = source ? latest.get(source.video_id) : null;
    hazard.textContent = latestRun
      ? 'Loading analysis…'
      : (source ? 'No visible access target' : 'Camera unavailable');
    const timestamp = document.createElement('time');
    timestamp.className = 'live-camera-time';
    timestamp.dataset.liveTimestamp = '';
    head.append(camera, hazard, timestamp);
    const screen = document.createElement('div');
    screen.className = 'live-screen';
    if (source) {
      const video = document.createElement('video');
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.src = api.videoUrl(source.video_id);
      video.addEventListener('loadedmetadata', updateLiveWall);
      video.addEventListener('error', () => card.classList.add('offline'));
      screen.append(video);
      const canvas = document.createElement('canvas');
      canvas.setAttribute('aria-hidden', 'true');
      screen.append(canvas);
    } else {
      screen.textContent = 'NO SIGNAL';
    }
    card.append(head, screen);
    root.append(card);
    if (source && latestRun) attachLiveAnalysis(screen.querySelector('video'), latestRun, hazard);
  }
  if (state.page === 'live') setLiveActive(true);
}

function listMessage(root, message) {
  const line = document.createElement('p');
  line.className = 'list-empty';
  line.textContent = message;
  root.append(line);
}

function overviewRow(title, detail, action, tone = '') {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = `overview-row ${tone}`;
  const copy = document.createElement('span');
  copy.className = 'overview-row-copy';
  const name = document.createElement('strong'); name.textContent = title;
  const sub = document.createElement('small'); sub.textContent = detail;
  copy.append(name, sub);
  const arrow = document.createElement('span');
  arrow.className = 'row-arrow'; arrow.textContent = '›'; arrow.setAttribute('aria-hidden', 'true');
  row.append(copy, arrow);
  row.addEventListener('click', action);
  return row;
}

function renderOverview() {
  const latest = latestReviewsByVideo();
  const reviewed = [...latest.values()];
  const videos = catalogVideos();
  const confirmed = reviewed.reduce((count, run) => count + (run.confirmed_count || 0), 0);
  const open = reviewed.reduce((count, run) => count + Math.max(0, (run.confirmed_count || 0) - (run.resolved_count || 0)), 0);
  $('metricOpen').textContent = String(open);
  $('metricConfirmed').textContent = String(confirmed);
  $('metricReviewed').textContent = String(reviewed.length);
  $('metricVideos').textContent = String(videos.length);
  $('attentionCount').textContent = open ? `${open} open` : 'Up to date';

  const attention = $('attentionList');
  attention.innerHTML = '';
  const needingAttention = reviewed.filter((run) => (run.confirmed_count || 0) > (run.resolved_count || 0));
  if (!needingAttention.length) listMessage(attention, 'No open findings in the latest completed review of each video.');
  for (const run of needingAttention) {
    const count = (run.confirmed_count || 0) - (run.resolved_count || 0);
    attention.append(overviewRow(runTitle(run), `${count} finding${count === 1 ? '' : 's'} without observed clearance · ${fmtDate(run.created_at)}`,
      () => loadRun(run.run_id), 'needs-attention'));
  }

  const recent = $('recentReviews');
  recent.innerHTML = '';
  if (!reviewed.length) listMessage(recent, 'No recorded-video reviews yet. Analyze a video to create the first one.');
  for (const run of reviewed.slice(0, 5)) {
    recent.append(overviewRow(runTitle(run), `${run.confirmed_count || 0} confirmed · ${run.resolved_count || 0} resolved · ${fmtDate(run.created_at)}`,
      () => loadRun(run.run_id)));
  }

  const awaiting = $('awaitingList');
  awaiting.innerHTML = '';
  const unreviewed = videos.filter((video) => !latest.has(video.video_id));
  if (!unreviewed.length) listMessage(awaiting, videos.length ? 'Every available video has a completed review.' : 'Videos from GitHub and uploads will appear here.');
  for (const video of unreviewed.slice(0, 8)) {
    awaiting.append(overviewRow(video.name, `${video.duration_s.toFixed(1)} s · ${video.kind === 'upload' ? 'Uploaded' : 'GitHub repository'}`,
      () => analyzeVideo(video.video_id)));
  }
}

function renderVideoCatalog() {
  const root = $('videoCatalog');
  root.innerHTML = '';
  const videos = catalogVideos();
  const latest = latestReviewsByVideo();
  if (!videos.length) { listMessage(root, 'No videos available yet. Sync GitHub or upload a recording.'); return; }
  for (const video of videos) {
    const run = latest.get(video.video_id);
    const card = document.createElement('article');
    card.className = 'video-card';
    const preview = document.createElement('div');
    preview.className = 'video-card-preview';
    const image = new Image();
    image.loading = 'lazy';
    image.alt = `Preview of ${video.name}`;
    image.src = api.videoFrameUrl(video.video_id, Math.min(1000, video.duration_s * 250), 720);
    image.onerror = () => { image.remove(); preview.classList.add('preview-unavailable'); };
    preview.append(image);
    const duration = document.createElement('span');
    duration.className = 'video-duration'; duration.textContent = `${video.duration_s.toFixed(1)} s`;
    preview.append(duration);
    const body = document.createElement('div'); body.className = 'video-card-body';
    const source = document.createElement('span'); source.className = 'video-source';
    source.textContent = video.kind === 'upload' ? 'UPLOAD' : 'GITHUB REPOSITORY';
    const title = document.createElement('h2'); title.textContent = video.name;
    const meta = document.createElement('p'); meta.className = 'video-card-meta';
    meta.textContent = `${video.width} × ${video.height} px · ${run ? `${run.confirmed_count || 0} findings in latest review` : 'Not reviewed yet'}`;
    const actions = document.createElement('div'); actions.className = 'card-actions';
    const analyze = document.createElement('button'); analyze.type = 'button'; analyze.className = 'btn btn-small';
    analyze.textContent = 'Analyze'; analyze.addEventListener('click', () => analyzeVideo(video.video_id));
    actions.append(analyze);
    if (run) {
      const view = document.createElement('button'); view.type = 'button'; view.className = 'btn btn-small btn-ghost';
      view.textContent = 'Open review'; view.addEventListener('click', () => loadRun(run.run_id));
      actions.append(view);
    }
    body.append(source, title, meta, actions);
    card.append(preview, body);
    root.append(card);
  }
}

async function analyzeVideo(videoId) {
  showPage('workspace');
  try { await selectVideo(videoId); openSetup(); } catch (error) { toast(`Could not open video: ${error.message}`); }
}

async function syncGithubVideos() {
  if (state.syncingVideos) return;
  state.syncingVideos = true;
  $('syncGithubBtn').disabled = true;
  $('githubSyncStatus').textContent = 'Checking GitHub for new videos…';
  try {
    const result = await api.syncGithubVideos();
    state.videos = await api.videos();
    renderVideoSelect();
    renderLiveGrid();
    renderVideoCatalog();
    renderOverview();
    const available = result.found - result.errors.length;
    $('githubSyncStatus').textContent = `${available} GitHub video${available === 1 ? '' : 's'} available · ${result.downloaded} newly imported${result.errors.length ? ` · ${result.errors.length} could not be imported` : ''}.`;
  } catch (error) {
    $('githubSyncStatus').textContent = error.status === 404
      ? 'Restart the ForeSmoke server to enable GitHub sync.'
      : `GitHub sync is unavailable: ${error.message}. Existing videos remain available.`;
  } finally {
    state.syncingVideos = false;
    $('syncGithubBtn').disabled = false;
  }
}

$('syncGithubBtn').addEventListener('click', syncGithubVideos);

function renderRuns() {
  const list = $('runList');
  list.innerHTML = '';
  const runs = visibleRuns();
  $('libraryCount').textContent = String(runs.filter((r) => r.state === 'done').length);
  const query = $('runSearch').value.trim().toLowerCase();
  const matching = runs.filter((r) => {
    if (state.libraryFilter === 'findings' && !(r.confirmed_count > 0)) return false;
    if (state.libraryFilter === 'clear' && !(r.state === 'done' && r.confirmed_count === 0)) return false;
    return !query || [runTitle(r), r.zone_name, r.run_id].some((value) => String(value || '').toLowerCase().includes(query));
  });
  if (!matching.length) {
    list.append(Object.assign(document.createElement('li'), { className: 'library-empty', textContent: runs.length ? 'No reviews match these filters.' : 'No reviews yet. Start with New review.' }));
    return;
  }
  for (const r of matching) {
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
  renderReviewSummary();
}

async function loadRun(runId) {
  if (annotator.active) stopDrawing();
  const previousPage = state.page;
  showPage('workspace');
  try {
    const [meta, states, events, observations, zones] = await Promise.all([
      api.run(runId), api.states(runId), api.events(runId).catch(() => []),
      api.observations(runId).catch(() => []), api.runZones(runId).catch(() => null),
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
    const openingTime = view.incidents[0]?.confirmed.evidence.t_ms ?? 0;
    player.seek(openingTime);
    panel.update(openingTime);
    const confirmed = view.incidents.length;
    const resolved = view.incidents.filter((i) => i.resolved).length;
    $('loadedRun').textContent = `${runTitle(meta)} · ${confirmed} confirmed, ${resolved} resolved`;
    renderRuns();
    updateRunButton();
    renderReviewSummary();
    closeSetup();
    showPage('workspace');
  } catch (error) {
    showPage(previousPage);
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

function updateGlobalRunStatus(label = 'Analyzing footage…') {
  $('globalRunStatus').hidden = !state.activeRunId;
  $('globalRunText').textContent = label;
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
    renderOverview();
    renderVideoCatalog();
  } catch (error) {
    showRunMessage('error', error.message);
  }
}

function trackRun(runId) {
  state.activeRunId = runId;
  updateGlobalRunStatus();
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
    updateGlobalRunStatus('Waiting for server…');
    state.pollTimer = setTimeout(poll, POLL_MS * 2);
    return;
  }
  const pct = Math.round(status.progress * 100);
  $('runProgress').querySelector('.progress span').style.width = `${pct}%`;
  $('runElapsed').textContent = `${Math.round(status.elapsed_s || 0)} s`;
  if (status.state === 'running') {
    updateGlobalRunStatus(status.processed_observations ? `Analyzing footage · ${pct}%` : 'Starting analysis…');
    $('runProgressText').textContent = status.processed_observations
      ? `Analysed ${status.processed_observations} of ~${status.expected_observations} frames (${pct}%)`
      : 'Starting analysis (loading the detection model)…';
    state.pollTimer = setTimeout(poll, POLL_MS);
    return;
  }
  state.activeRunId = null;
  updateGlobalRunStatus();
  $('runProgress').hidden = true;
  updateRunButton();
  state.runs = await api.runs().catch(() => state.runs);
  renderRuns();
  renderOverview();
  renderLiveGrid();
  renderVideoCatalog();
  renderReviewSummary();
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
      toast('Analysis cancelled.', 'warn');
    } else {
      showRunMessage('error', status.error || 'Analysis failed.', details);
      toast(status.error || 'Analysis failed. Open New review for details.');
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
  if (e.key === 'Escape') {
    if (!$('libraryPanel').hidden) { closeLibrary(); e.preventDefault(); }
    else if (annotator.active) { stopDrawing(); e.preventDefault(); }
    else if (!$('setupPanel').hidden) { closeSetup(); e.preventDefault(); }
    return;
  }
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.tagName === 'BUTTON')) return;
  if (e.key === ' ') { e.preventDefault(); if (!annotator.active) player.toggle(); }
  if (e.key === 'ArrowLeft') { e.preventDefault(); player.step(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); player.step(1); }
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
    $('stageEmpty').textContent = 'Could not load recordings. Check the server, then refresh this page.';
    toast(`Could not reach the ForeSmoke server: ${error.message}`);
    return;
  }
  renderVideoSelect();
  renderZones();
  renderRuns();
  renderReviewSummary();
  renderOverview();
  renderVideoCatalog();
  renderLiveGrid();
  showPage('overview');
  const running = state.runs.find((r) => r.state === 'running');
  if (running) trackRun(running.run_id);
  $('stageEmpty').textContent = 'Choose a video from Videos or start a new review.';
  updateRunButton();
  syncGithubVideos();
}

boot();
