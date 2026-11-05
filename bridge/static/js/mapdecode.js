/* Camera-mapping decoder: from the photos of the bridge's Gray-coded frames to one (x, y) per
 * LED, plus the colour order each LED showed. Pure functions; runs in the phone's browser
 * (window.MapDecode) and in Node for the tests (module.exports).
 *
 * Frames are W*H arrays (luminance 0..255) captured for each step the bridge lists:
 *   black, white, white_end : luminance
 *   colour.black / .red / .green / .blue : {r, g, b} arrays (the frame in camera colour)
 *   bits[k] = [A, B] : LEDs whose Gray-code bit k is 1, then the inverse
 * LED global index g has code gray(g + 1); 0 means "no LED".
 */
(function (root) {
  'use strict';

  function grayDecode(v) { let b = v; for (let s = v >> 1; s; s >>= 1) b ^= s; return b; }

  function decode(f, total, opts) {
    const o = Object.assign({minOn: 20, minConfAbs: 6, minConfRel: 0.25, minPixels: 2}, opts || {});
    const W = f.W, H = f.H, N = W * H, nb = f.bits.length;
    const idx = new Int32Array(N).fill(-1), wt = new Float32Array(N);
    const count = new Int32Array(total), seed = new Int32Array(total).fill(-1), best = new Float32Array(total);
    let litPixels = 0, decoded = 0;
    for (let p = 0; p < N; p++) {
      const on = f.white[p] - f.black[p];
      if (on < o.minOn) continue;
      litPixels++;
      let code = 0, conf = 1e9;
      for (let k = 0; k < nb; k++) {
        const d = f.bits[k][0][p] - f.bits[k][1][p];
        if (d > 0) code |= 1 << k;
        const a = d < 0 ? -d : d;
        if (a < conf) conf = a;
      }
      if (conf < o.minConfAbs || conf < o.minConfRel * on) continue;
      const g = grayDecode(code) - 1;
      if (g < 0 || g >= total) continue;
      idx[p] = g; wt[p] = conf; count[g]++; decoded++;
      if (conf > best[g]) { best[g] = conf; seed[g] = p; }
    }
    // second pass: weighted centroid of each LED's pixels near its most confident pixel,
    // which drops stray pixels elsewhere in the frame that happened to decode to it
    const sx = new Float64Array(total), sy = new Float64Array(total), sw = new Float64Array(total), n2 = new Int32Array(total);
    const r2 = new Float32Array(total);
    for (let g = 0; g < total; g++) { const r = 2 + 1.5 * Math.sqrt(count[g] / Math.PI); r2[g] = r * r; }
    for (let p = 0; p < N; p++) {
      const g = idx[p];
      if (g < 0) continue;
      const s = seed[g], x = p % W, y = (p / W) | 0, dx = x - s % W, dy = y - ((s / W) | 0);
      if (dx * dx + dy * dy > r2[g]) continue;
      sx[g] += x * wt[p]; sy[g] += y * wt[p]; sw[g] += wt[p]; n2[g]++;
    }
    const pts = new Array(total).fill(null);
    for (let g = 0; g < total; g++)
      if (n2[g] >= o.minPixels && sw[g] > 0) pts[g] = [sx[g] / sw[g], sy[g] / sw[g]];

    // chain sanity: an LED far from both chain neighbours, which are close to each other, is
    // almost certainly a mis-decode; drop it (the bridge fills it in from its neighbours)
    const steps = [];
    for (let g = 1; g < total; g++) if (pts[g] && pts[g - 1]) steps.push(dist(pts[g], pts[g - 1]));
    steps.sort((a, b) => a - b);
    const m = steps.length ? Math.max(1, steps[steps.length >> 1]) : 1;
    let outliers = 0;
    for (let g = 1; g < total - 1; g++) {
      const a = pts[g - 1], b = pts[g], c = pts[g + 1];
      if (a && b && c && dist(a, b) > 6 * m && dist(b, c) > 6 * m && dist(a, c) < 4 * m) { pts[g] = null; outliers++; }
    }

    // colour order: which camera channel answered each wire channel, at each found LED
    const colours = new Array(total).fill(null);
    if (f.colour) {
      const C = f.colour, chans = ['r', 'g', 'b'], sent = [C.red, C.green, C.blue];
      for (let g = 0; g < total; g++) {
        const pt = pts[g];
        if (!pt) continue;
        const cx = Math.round(pt[0]), cy = Math.round(pt[1]);
        let s = '';
        for (let k = 0; k < 3 && s !== null; k++) {
          const resp = [0, 0, 0];
          for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
            const x = cx + dx, y = cy + dy;
            if (x < 0 || y < 0 || x >= W || y >= H) continue;
            const p = y * W + x;
            for (let j = 0; j < 3; j++) resp[j] += Math.max(0, sent[k][chans[j]][p] - C.black[chans[j]][p]);
          }
          const order = [0, 1, 2].sort((a, b) => resp[b] - resp[a]);
          s = resp[order[0]] > 20 && resp[order[0]] >= 1.3 * resp[order[1]] ? s + 'RGB'[order[0]] : null;
        }
        colours[g] = s;
      }
    }

    // did the camera move? compare the white frames from the start and the end
    let diff = 0, onSum = 0;
    if (f.whiteEnd) for (let p = 0; p < N; p++) {
      const on = f.white[p] - f.black[p];
      if (on < o.minOn) continue;
      diff += Math.abs(f.white[p] - f.whiteEnd[p]); onSum += on;
    }
    const moved = onSum ? diff / onSum : 0;
    const found = pts.reduce((n, p) => n + (p ? 1 : 0), 0);
    return {
      points: pts.map(p => p && [p[0] / W, p[1] / W]),      // normalised by width: x 0..1, y keeps the aspect
      pixels: pts, colours,
      stats: {total, found, outliers, litPixels, decoded, moved: Math.round(moved * 100) / 100,
              cameraMoved: moved > 0.35, stepPx: Math.round(m * 10) / 10},
    };
  }

  function dist(a, b) { const dx = a[0] - b[0], dy = a[1] - b[1]; return Math.sqrt(dx * dx + dy * dy); }

  const api = {decode, grayDecode};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MapDecode = api;
})(typeof window !== 'undefined' ? window : this);
