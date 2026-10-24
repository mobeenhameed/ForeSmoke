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

const ascii = (value) => String(value ?? '—').normalize('NFKD').replace(/[^\x20-\x7e]/g, '?');
const pdfEscape = (value) => ascii(value).replace(/([\\()])/g, '\\$1');

function wrap(text, width = 88) {
  const words = ascii(text).split(/\s+/);
  const lines = [];
  let line = '';
  for (const word of words) {
    if (!line) line = word;
    else if (`${line} ${word}`.length <= width) line += ` ${word}`;
    else { lines.push(line); line = word; }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [''];
}

/** Build a dependency-free, portable PDF summary. */
export function buildPdfBlob(report) {
  const lines = [
    'ForeSmoke footage review',
    `Generated: ${report.generated_at}`,
    `Run: ${report.run_id}`,
    `Source: ${report.source.name || 'synthetic or external'}`,
    `Camera / view: ${report.camera_id || '-'} / ${report.view_id || '-'}`,
    '',
    `Confirmed: ${report.summary.confirmed}   Resolved: ${report.summary.resolved}   Open: ${report.summary.open}`,
    '',
  ];
  for (const incident of report.incidents) {
    lines.push(
      `Incident ${incident.incident_id}`,
      `Zone: ${incident.zone_id}   Objects: ${incident.object_labels.join(', ') || 'unspecified'}`,
      `First seen: ${incident.first_seen_ms} ms   Confirmed: ${incident.confirmed_at_ms} ms   Resolved: ${incident.resolved_at_ms ?? 'not observed'}`,
      `Reason: ${incident.confirmation_reason}`,
      '',
    );
  }
  lines.push(...wrap(report.scope));
  const wrapped = lines.flatMap((line) => wrap(line));
  const pageLines = [];
  for (let index = 0; index < wrapped.length; index += 48) pageLines.push(wrapped.slice(index, index + 48));
  if (!pageLines.length) pageLines.push(['ForeSmoke footage review']);

  const fontId = 3 + pageLines.length * 2;
  const pageIds = pageLines.map((_, index) => 3 + index * 2);
  const objects = new Array(fontId + 1);
  objects[1] = '<< /Type /Catalog /Pages 2 0 R >>';
  objects[2] = `<< /Type /Pages /Count ${pageIds.length} /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] >>`;
  pageLines.forEach((page, index) => {
    const pageId = pageIds[index];
    const contentId = pageId + 1;
    const commands = `BT\n/F1 10 Tf\n46 760 Td\n13 TL\n${page.map((line) => `(${pdfEscape(line)}) Tj\nT*`).join('\n')}\nET`;
    objects[pageId] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${fontId} 0 R >> >> /Contents ${contentId} 0 R >>`;
    objects[contentId] = `<< /Length ${commands.length} >>\nstream\n${commands}\nendstream`;
  });
  objects[fontId] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>';

  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  for (let id = 1; id <= fontId; id += 1) {
    offsets[id] = pdf.length;
    pdf += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xref = pdf.length;
  pdf += `xref\n0 ${fontId + 1}\n0000000000 65535 f \n`;
  for (let id = 1; id <= fontId; id += 1) pdf += `${String(offsets[id]).padStart(10, '0')} 00000 n \n`;
  pdf += `trailer\n<< /Size ${fontId + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new Blob([pdf], { type: 'application/pdf' });
}
