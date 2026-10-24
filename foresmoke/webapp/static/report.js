// Build a portable review summary from records the engine already emitted.
// All times refer to the source video, not processing wall time.

export function buildReport(view, generatedAt = new Date().toISOString()) {
  const incidents = view.incidents.map((item) => {
    const confirmed = item.confirmed;
    const resolved = item.resolved;
    return {
      incident_id: item.incident_id,
      zone_id: item.zone_id,
      object_labels: [...new Set((confirmed.evidence.objects || []).map((object) => object.label))],
      first_seen_ms: confirmed.first_seen_ms,
      confirmed_at_ms: confirmed.confirmed_at_ms,
      resolved_at_ms: resolved?.resolved_at_ms ?? null,
      confirmation_evidence_ms: confirmed.confirmation_evidence_ms,
      clear_evidence_ms: resolved?.clear_evidence_ms ?? null,
      confirmation_reason: confirmed.reason_code,
      resolution_reason: resolved?.reason_code ?? null,
      confirmation_evidence: confirmed.evidence,
      resolution_evidence: resolved?.evidence ?? null,
    };
  });
  return {
    format_version: '1',
    product: 'ForeSmoke',
    generated_at: generatedAt,
    run_id: view.meta.run_id,
    source: {
      name: view.meta.video_name || null,
      kind: view.meta.video_id ? 'recorded_video' : 'synthetic_or_external',
      duration_s: view.meta.duration_s ?? null,
    },
    camera_id: view.zoneConfig?.camera_id ?? null,
    view_id: view.zoneConfig?.view_id ?? null,
    zones: (view.zoneConfig?.zones || []).map((zone) => ({
      zone_id: zone.zone_id,
      candidate_labels: zone.candidate_labels,
      confirm_ms: zone.confirm_ms,
      resolve_ms: zone.resolve_ms,
      min_confidence: zone.min_confidence,
    })),
    summary: {
      confirmed: incidents.length,
      resolved: incidents.filter((item) => item.resolved_at_ms !== null).length,
      open: incidents.filter((item) => item.resolved_at_ms === null).length,
    },
    incidents,
    scope: 'Configured visual zone occupancy only. This report does not establish physical clearance or legal safety compliance.',
  };
}
