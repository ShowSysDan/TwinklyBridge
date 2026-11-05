/* Camera-mapping decoder: from the photos of the bridge's Gray-coded frames to one (x, y) per
 * LED, plus the colour order each LED showed. Pure functions; runs in the phone's browser
 * (window.MapDecode) and in Node for the tests (module.exports).
 *
 * Frames are W*H arrays (luminance 0..255) captured for each step the bridge lists:
 *   black, white, white_end : luminance
 *   colour.black / .red / .green / .blue : {r, g, b} arrays (the frame in camera colour)
 *   bits[k] = [A, B] : LEDs whose Gray-code bit k is 1, then the inverse
 * LED global index g has code gray(g + 1); 0 means "no LED".
 *
 * Each camera pixel reads its own LED number (one bit per pattern pair: pattern minus inverse),
 * keeping only pixels where every bit is clear. Each LED is then the centre of its pixels near
 * its most confident one. Pixel by pixel copes with LEDs only 2-3 camera pixels apart, where
 * finding spots first would merge neighbours (tested).
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

  // How different two captures are where LEDs can be: mean |a - b| over pixels lit in white,
  // as a share of their on-brightness. The phone uses it to catch a photo taken before the
  // LEDs had changed (camera lag), and takes it again.
  function change(a, b, white, black) {
    let d = 0, s = 0;
    for (let p = 0; p < a.length; p += 3) {
      const o = white[p] - black[p];
      if (o < 20) continue;
      d += Math.abs(a[p] - b[p]); s += o;
    }
    return s ? d / s : 1;
  }

  // ---------------------------------------------------------------- stabilising
  // A hand-held phone drifts a few pixels over the ~25 photos of a capture. Every coding photo
  // lights a subset of the LEDs lit in the all-on photo, so sliding a photo until its lit
  // pixels land on the all-on photo's lit pixels gives the drift; shifting it back lines it
  // up. Coarse search on a quarter-size picture, then +-4 px at full size, then a parabola
  // through the scores for the fraction of a pixel. Only pixels where LEDs are (from all-on
  // minus all-off) are summed, which also makes it fast.

  function quarter(a, W, H) {
    const w = W >> 2, h = H >> 2, o = new Float32Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0;
      for (let dy = 0; dy < 4; dy++) { const r = (4 * y + dy) * W + 4 * x; s += a[r] + a[r + 1] + a[r + 2] + a[r + 3]; }
      o[y * w + x] = s / 16;
    }
    return {d: o, w, h};
  }

  // Where the lights are: pixel lists (full size and quarter size, thinned to `max`) and the
  // bounding box of the LED area (the prop's edges in the picture).
  function lights(white, black, W, H, T = 30, max = 12000) {
    const all = [];
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let p = 0; p < W * H; p++) {
      if (white[p] - black[p] < T) continue;
      all.push(p);
      const x = p % W, y = (p / W) | 0;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    const step = Math.max(1, Math.ceil(all.length / max));
    const pts = all.filter((_, i) => i % step === 0);
    const seen = new Set(), ptsQ = [];
    const w = W >> 2;
    for (const p of all) { const q = ((p / W / 4) | 0) * w + ((p % W) >> 2); if (!seen.has(q)) { seen.add(q); ptsQ.push(q); } }
    return {pts, ptsQ, count: all.length, box: x1 < 0 ? null : [x0, y0, x1, y1]};
  }

  // Drift of `img` against the all-on reference: {dx, dy, z}. z: how far the best score stands
  // out from the other candidates (in standard deviations); below ~4 the photo has too little
  // in it to tell (e.g. the all-off photo).
  function shiftOf(img, W, H, L, maxShift = 48) {
    const q = quarter(img, W, H), m = Math.ceil(maxShift / 4), scores = [];
    let best = -Infinity, bx = 0, by = 0;
    for (let dy = -m; dy <= m; dy++) for (let dx = -m; dx <= m; dx++) {
      let s = 0;
      for (const p of L.ptsQ) {
        const x = p % q.w + dx, y = ((p / q.w) | 0) + dy;
        if (x >= 0 && y >= 0 && x < q.w && y < q.h) s += q.d[y * q.w + x];
      }
      scores.push(s);
      if (s > best) { best = s; bx = dx; by = dy; }
    }
    const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
    const sd = Math.sqrt(scores.reduce((a, b) => a + (b - mean) ** 2, 0) / scores.length) || 1;
    const z = (best - mean) / sd;
    // refine at full size
    const fine = (dx, dy) => {
      let s = 0;
      for (const p of L.pts) {
        const x = p % W + dx, y = ((p / W) | 0) + dy;
        if (x >= 0 && y >= 0 && x < W && y < H) s += img[y * W + x];
      }
      return s;
    };
    let fb = -Infinity, fx = 4 * bx, fy = 4 * by;
    const cx = 4 * bx, cy = 4 * by;
    for (let dy = cy - 4; dy <= cy + 4; dy++) for (let dx = cx - 4; dx <= cx + 4; dx++) {
      const s = fine(dx, dy);
      if (s > fb) { fb = s; fx = dx; fy = dy; }
    }
    const para = (a, b, c) => { const d = a - 2 * b + c; return d < 0 ? Math.max(-0.5, Math.min(0.5, (a - c) / (2 * d))) : 0; };
    const sx = para(fine(fx - 1, fy), fb, fine(fx + 1, fy)), sy = para(fine(fx, fy - 1), fb, fine(fx, fy + 1));
    return {dx: fx + sx, dy: fy + sy, z: Math.round(z * 10) / 10};
  }

  // `a` sampled at (x + dx, y + dy): undoes a drift of (dx, dy). Bilinear; outside -> 0.
  function shifted(a, W, H, dx, dy) {
    const o = new Uint8ClampedArray(W * H), ix = Math.floor(dx), iy = Math.floor(dy), fx = dx - ix, fy = dy - iy;
    for (let y = 0; y < H; y++) {
      const y0 = y + iy, y1 = y0 + 1;
      if (y0 < 0 || y1 >= H) continue;
      for (let x = 0; x < W; x++) {
        const x0 = x + ix, x1 = x0 + 1;
        if (x0 < 0 || x1 >= W) continue;
        o[y * W + x] = (a[y0 * W + x0] * (1 - fx) + a[y0 * W + x1] * fx) * (1 - fy)
                     + (a[y1 * W + x0] * (1 - fx) + a[y1 * W + x1] * fx) * fy;
      }
    }
    return o;
  }

  // Line every photo of a capture up with the all-on one. shots: {step: {W, H, lum, (r, g, b)}}.
  // Returns {shots (aligned copies), shifts: {step: [dx, dy, z]}, unaligned: [steps that
  // couldn't be told and took their neighbour's drift], drift (largest, px), box}.
  function stabilise(shots, opts) {
    const o = Object.assign({minZ: 4, maxShift: 48}, opts || {});
    const W = shots.white.W, H = shots.white.H;
    const L = lights(shots.white.lum, shots.black.lum, W, H);
    const out = {white: shots.white}, shifts = {white: [0, 0, 99]}, unaligned = [];
    const names = Object.keys(shots).filter(n => n !== 'white');
    let last = {dx: 0, dy: 0};
    const est = {};
    for (const n of names) {
      if (n === 'black') continue;                     // nothing lit to line up with
      const s = shiftOf(shots[n].lum, W, H, L, o.maxShift);
      if (s.z < o.minZ) { unaligned.push(n); est[n] = {dx: last.dx, dy: last.dy, z: s.z}; }
      else { est[n] = s; last = s; }
    }
    // the all-off photo was taken between all-on and the colour photos: use the nearest drift
    const near = names.find(n => n !== 'black' && est[n] && est[n].z >= o.minZ);
    est.black = near ? {dx: est[near].dx, dy: est[near].dy, z: 0} : {dx: 0, dy: 0, z: 0};
    let drift = 0;
    for (const n of names) {
      const {dx, dy, z} = est[n], sh = shots[n];
      shifts[n] = [Math.round(dx * 10) / 10, Math.round(dy * 10) / 10, z];
      drift = Math.max(drift, Math.hypot(dx, dy));
      if (Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05) { out[n] = sh; continue; }
      const c = {W, H, lum: shifted(sh.lum, W, H, dx, dy)};
      if (sh.r) { c.r = shifted(sh.r, W, H, dx, dy); c.g = shifted(sh.g, W, H, dx, dy); c.b = shifted(sh.b, W, H, dx, dy); }
      if (sh.chroma) c.chroma = shifted(sh.chroma, W, H, dx, dy);
      out[n] = c;
    }
    return {shots: out, shifts, unaligned, drift: Math.round(drift * 10) / 10, box: L.box, lit: L.count};
  }

  // Green-ness of a pixel, 0..255: how much greener it is than red and blue. The coding
  // patterns are pure green, and in a lit room almost nothing else is: white walls, grey
  // concrete and copper tinsel score about 0 however bright, so the LEDs stand out where
  // brightness alone hardly separates them from the room. (A clipped, white-hot LED centre
  // scores 0 too, which is why each capture is decoded both ways and the better one kept.)
  function greenness(r, g, b) {
    const v = 1.6 * (g - 0.5 * (r + b));
    return v <= 0 ? 0 : v >= 255 ? 255 : v;
  }

  // One photo's luminance lined up with the all-on photo (for checks during a capture).
  function alignedLum(img, W, H, L) {
    const s = shiftOf(img, W, H, L);
    return s.z < 4 ? img : shifted(img, W, H, s.dx, s.dy);
  }

  // Decode a capture by brightness and by green-ness, and keep the better one (more LEDs).
  // shots as from stabilise(); each photo has lum and (for green-ness) chroma.
  function decodeBest(A, bits, total, opts) {
    const make = key => {
      const f = {W: A.white.W, H: A.white.H, black: A.black[key], white: A.white[key], whiteEnd: A.white_end[key], bits: [],
                 colour: {black: A.black, red: A.red, green: A.green, blue: A.blue}};
      for (let k = 0; k < bits; k++) f.bits.push([A['b' + k][key], A['i' + k][key]]);
      return f;
    };
    const byLum = decode(make('lum'), total, opts);
    if (!A.white.chroma) return Object.assign(byLum, {signal: 'brightness', tried: {brightness: byLum.stats.found}});
    const byGreen = decode(make('chroma'), total, opts);
    const best = byGreen.stats.found > byLum.stats.found ? Object.assign(byGreen, {signal: 'green-ness'})
                                                         : Object.assign(byLum, {signal: 'brightness'});
    best.tried = {brightness: byLum.stats.found, 'green-ness': byGreen.stats.found};
    return best;
  }

  const api = {decode, decodeBest, grayDecode, change, stabilise, shiftOf, lights, alignedLum, greenness};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MapDecode = api;
})(typeof window !== 'undefined' ? window : this);
