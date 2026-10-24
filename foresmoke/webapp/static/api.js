// Thin REST client. Every engine record is passed through exactly as the backend wrote it.

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = Array.isArray(body.detail)
          ? body.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
          : String(body.detail);
      }
    } catch (_) { /* non-JSON error body */ }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response;
}

const json = async (path, options) => (await request(path, options)).json();
const post = (path, body) => json(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** Parse JSONL text into records without renaming or recomputing any field. */
export function parseJsonl(text) {
  const records = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      records.push(JSON.parse(trimmed));
    } catch (_) {
      // A run that is still being written can end in a partial line; skip it.
    }
  }
  return records;
}

async function jsonl(path) {
  try {
    return parseJsonl(await (await request(path, { cache: 'no-store' })).text());
  } catch (error) {
    if (error.status === 404) return [];
    throw error;
  }
}

export const api = {
  videos: () => json('/api/videos'),
  syncGithubVideos: () => json('/api/videos/github-sync', { method: 'POST' }),
  uploadVideo(file, onProgress) {
    // XMLHttpRequest exposes upload progress; fetch does not.
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append('file', file);
      xhr.open('POST', '/api/videos');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error((body && body.detail) || `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error('Upload failed: the server could not be reached.'));
      xhr.send(form);
    });
  },
  videoUrl: (id) => `/api/videos/${encodeURIComponent(id)}/file`,
  videoFrameUrl: (id, tMs = 0, width = 480) => `/api/videos/${encodeURIComponent(id)}/frame?t_ms=${Math.max(0, Math.round(tMs))}&width=${width}`,
  zones: () => json('/api/zones'),
  validateZone: (body) => post('/api/zones/validate', body),
  saveZone: (body) => post('/api/zones', body),
  suggestZones: (body) => post('/api/zone-proposals', body),
  zoneProposal: (id) => json(`/api/zone-proposals/${encodeURIComponent(id)}`),
  zoneProposalFrameUrl: (id) => `/api/zone-proposals/${encodeURIComponent(id)}/frame`,
  cancelZoneProposal: (id) => json(`/api/zone-proposals/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  acceptZoneProposal: (id, body) => post(`/api/zone-proposals/${encodeURIComponent(id)}/accept`, body),
  runs: () => json('/api/runs'),
  run: (id) => json(`/api/runs/${encodeURIComponent(id)}`),
  startRun: (videoId, zoneConfigId) => post('/api/runs', { video_id: videoId, zone_config_id: zoneConfigId }),
  cancelRun: (id) => json(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  runLog: async (id) => (await request(`/api/runs/${encodeURIComponent(id)}/log`)).text(),
  async runZones(id) {
    try { return await json(`/api/runs/${encodeURIComponent(id)}/zones`); } catch (e) {
      if (e.status === 404) return null;
      throw e;
    }
  },
  observations: (id) => jsonl(`/api/runs/${encodeURIComponent(id)}/observations`),
  states: (id) => jsonl(`/api/runs/${encodeURIComponent(id)}/states`),
  events: (id) => jsonl(`/api/runs/${encodeURIComponent(id)}/events`),
  thumbUrl: (id, tMs, width = 360) => `/api/runs/${encodeURIComponent(id)}/thumb?t_ms=${Math.max(0, Math.round(tMs))}&width=${width}`,
};
