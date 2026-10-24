// Client-side pre-validation of a zone polygon in normalized [0, 1] coordinates.
// This mirrors what the engine rejects (contract + Shapely validity) so problems show up
// while drawing. The server re-validates with the engine's own validator before saving.

const EPS = 1e-12;

export const round6 = (v) => Math.round(v * 1e6) / 1e6;

export function signedArea(points) {
  let sum = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    sum += x1 * y2 - x2 * y1;
  }
  return sum / 2;
}

const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
const onSegment = (p, a, b) => (
  Math.min(a[0], b[0]) - EPS <= p[0] && p[0] <= Math.max(a[0], b[0]) + EPS
  && Math.min(a[1], b[1]) - EPS <= p[1] && p[1] <= Math.max(a[1], b[1]) + EPS
);

/** True when closed segments ab and cd share any point (proper crossing or touching). */
export function segmentsIntersect(a, b, c, d) {
  const d1 = cross(c, d, a);
  const d2 = cross(c, d, b);
  const d3 = cross(a, b, c);
  const d4 = cross(a, b, d);
  if (((d1 > EPS && d2 < -EPS) || (d1 < -EPS && d2 > EPS))
      && ((d3 > EPS && d4 < -EPS) || (d3 < -EPS && d4 > EPS))) return true;
  if (Math.abs(d1) <= EPS && onSegment(a, c, d)) return true;
  if (Math.abs(d2) <= EPS && onSegment(b, c, d)) return true;
  if (Math.abs(d3) <= EPS && onSegment(c, a, b)) return true;
  if (Math.abs(d4) <= EPS && onSegment(d, a, b)) return true;
  return false;
}

/** Indices of edges (edge i runs from vertex i to i+1) that take part in a self-intersection. */
export function badEdges(points) {
  const n = points.length;
  const bad = new Set();
  if (n < 3) return bad;
  const edge = (i) => [points[i], points[(i + 1) % n]];
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 1; j < n; j += 1) {
      const adjacent = j === i + 1 || (i === 0 && j === n - 1);
      const [a, b] = edge(i);
      const [c, d] = edge(j);
      if (adjacent) {
        // Adjacent edges share a vertex; they are only invalid if they fold back onto each other.
        const shared = j === i + 1 ? b : a;
        const p = j === i + 1 ? a : b;
        const q = j === i + 1 ? d : c;
        if (Math.abs(cross(shared, p, q)) <= EPS) {
          const dot = (p[0] - shared[0]) * (q[0] - shared[0]) + (p[1] - shared[1]) * (q[1] - shared[1]);
          if (dot > 0) { bad.add(i); bad.add(j); }
        }
      } else if (segmentsIntersect(a, b, c, d)) {
        bad.add(i); bad.add(j);
      }
    }
  }
  return bad;
}

/** Returns { ok, errors[], warnings[], badEdges:Set } for a normalized polygon. */
export function validatePolygon(points) {
  const errors = [];
  const warnings = [];
  if (points.length < 3) errors.push(`Add at least 3 corners (${points.length} placed).`);
  if (points.some(([x, y]) => !(x >= 0 && x <= 1 && y >= 0 && y <= 1))) {
    errors.push('Every corner must be inside the video frame.');
  }
  const keys = points.map(([x, y]) => `${round6(x)},${round6(y)}`);
  if (new Set(keys).size !== keys.length) errors.push('Two corners are in the same place; move or delete one.');
  let bad = new Set();
  if (points.length >= 3) {
    bad = badEdges(points);
    if (bad.size) errors.push('Edges cross each other. Drag a corner so the outline does not self-intersect.');
    const area = Math.abs(signedArea(points));
    if (area <= 1e-9) errors.push('The zone has no area; spread the corners out.');
    else if (area < 0.002) warnings.push('The zone is very small (under 0.2% of the frame).');
  }
  return { ok: errors.length === 0, errors, warnings, badEdges: bad };
}
