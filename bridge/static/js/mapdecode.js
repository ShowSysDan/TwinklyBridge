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
 * Everything here works on the BAND-PASSED picture, never on raw brightness. In a lit room most
 * of the light at an LED's position is its own light scattered back off the tinsel by the other
 * ~625 lit LEDs: measured on a real capture, only ~27% of the raw level at a core is that LED.
 * That scattered glow is a smooth, low-frequency field and an LED is a ~3 px dot, so a top-hat
 * (3x3 core minus the ring around it) separates them almost completely: after it, ~90% of what
 * is left at a core is the LED's own light. Decide every bit on the top-hat, not the raw pixel.
 *
 * Photos are found first, then decoded per spot. The phone drifts ~20 px over a 25 s capture and
 * 1 px of registration error costs ~70% of a 3 px dot's amplitude, so each photo is lined up
 * against the all-on photo with an AFFINE fit (a hand aimed up at a prop rolls a few tenths of a
 * degree; a plain translation leaves up to 7 px of residual across the frame and loses ~45% of
 * the LEDs). Alignment is scored by matching band-passed pictures, never by maximising
 * brightness: a brightness score slides each pattern onto its own lit subset.
 */
(function (root) {
  'use strict';

  function grayDecode(v) { let b = v; for (let s = v >> 1; s; s >>= 1) b ^= s; return b; }
  function dist(a, b) { const dx = a[0] - b[0], dy = a[1] - b[1]; return Math.sqrt(dx * dx + dy * dy); }

  // ------------------------------------------------------------------ box filters
  // Integral image, so a box of any radius costs the same four lookups per pixel.
  function integral(a, W, H) {
    const I = new Float64Array((W + 1) * (H + 1));
    for (let y = 0; y < H; y++) {
      let run = 0;
      for (let x = 0; x < W; x++) {
        run += a[y * W + x];
        I[(y + 1) * (W + 1) + x + 1] = I[y * (W + 1) + x + 1] + run;
      }
    }
    return I;
  }
  // Mean of the (2r+1)^2 box round each pixel, clipped at the edges.
  function boxMean(a, W, H, r, I) {
    I = I || integral(a, W, H);
    const o = new Float32Array(W * H), S = W + 1;
    for (let y = 0; y < H; y++) {
      const y0 = y - r < 0 ? 0 : y - r, y1 = y + r + 1 > H ? H : y + r + 1;
      for (let x = 0; x < W; x++) {
        const x0 = x - r < 0 ? 0 : x - r, x1 = x + r + 1 > W ? W : x + r + 1;
        o[y * W + x] = (I[y1 * S + x1] - I[y0 * S + x1] - I[y1 * S + x0] + I[y0 * S + x0])
                     / ((y1 - y0) * (x1 - x0));
      }
    }
    return o;
  }
  // Band-pass: structure a few pixels across, with the smooth glow and any whole-frame
  // exposure or white-balance shift removed. Used for every alignment score.
  function bandpass(a, W, H) {
    const I = integral(a, W, H), n = boxMean(a, W, H, 1, I), f = boxMean(a, W, H, 4, I);
    const o = new Float32Array(W * H);
    for (let p = 0; p < o.length; p++) o[p] = n[p] - f[p];
    return o;
  }
  // Top-hat: the 3x3 core minus the ring out to r=7. An LED's own light survives; the glow
  // from every other lit LED, which is smooth on that scale, cancels.
  const TH_R = 7;
  function tophat(a, W, H) {
    const I = integral(a, W, H), S = W + 1, o = new Float32Array(W * H);
    for (let y = 0; y < H; y++) {
      const ay0 = y - 1 < 0 ? 0 : y - 1, ay1 = y + 2 > H ? H : y + 2;
      const by0 = y - TH_R < 0 ? 0 : y - TH_R, by1 = y + TH_R + 1 > H ? H : y + TH_R + 1;
      for (let x = 0; x < W; x++) {
        const ax0 = x - 1 < 0 ? 0 : x - 1, ax1 = x + 2 > W ? W : x + 2;
        const bx0 = x - TH_R < 0 ? 0 : x - TH_R, bx1 = x + TH_R + 1 > W ? W : x + TH_R + 1;
        const cs = I[ay1 * S + ax1] - I[ay0 * S + ax1] - I[ay1 * S + ax0] + I[ay0 * S + ax0];
        const cn = (ay1 - ay0) * (ax1 - ax0);
        const os = I[by1 * S + bx1] - I[by0 * S + bx1] - I[by1 * S + bx0] + I[by0 * S + bx0];
        const on = (by1 - by0) * (bx1 - bx0);
        o[y * W + x] = cs / cn - (os - cs) / (on - cn);
      }
    }
    return o;
  }

  // ------------------------------------------------------------------ finding the LEDs
  // Spots: local maxima of the top-hat of (all-on minus all-off). Each is one LED's dot.
  // The old mask ("brighter than 30") took 17.5% of a lit-room frame -- the dots plus the glow
  // over all the tinsel plus every edge in the room -- which is useless both for alignment and
  // as a spot list, and is what painted the live preview solid green.
  const SPOT_T = 12, SPOT_MARGIN = 8, NMS = 2;
  // "auto": four times the noise of the top-hat itself, from the picture in hand. Median
  // absolute deviation, so the dots themselves do not raise it. Self-calibrating, so it holds
  // in a dark room and in a lit warehouse, and it does not run away when a shaky hand turns
  // every edge in the room into top-hat response.
  function spotThreshold(th) {
    const s = [];
    for (let i = 0; i < th.length; i += 7) s.push(th[i]);
    s.sort((a, b) => a - b);
    const med = s[s.length >> 1], dev = [];
    for (let i = 0; i < s.length; i += 3) dev.push(Math.abs(s[i] - med));
    dev.sort((a, b) => a - b);
    return Math.max(8, med + 4 * 1.4826 * dev[dev.length >> 1]);
  }
  function findSpots(on, W, H, T) {
    const th = tophat(on, W, H), out = [];
    T = T === 'auto' ? spotThreshold(th) : T === undefined ? SPOT_T : T;
    for (let y = SPOT_MARGIN; y < H - SPOT_MARGIN; y++) {
      for (let x = SPOT_MARGIN; x < W - SPOT_MARGIN; x++) {
        const p = y * W + x, v = th[p];
        if (v < T) continue;
        let top = true;
        for (let dy = -NMS; dy <= NMS && top; dy++) {
          for (let dx = -NMS; dx <= NMS; dx++) {
            if (!dx && !dy) continue;
            const q = (y + dy) * W + x + dx;
            if (th[q] > v || (th[q] === v && q < p)) { top = false; break; }
          }
        }
        if (top) out.push({x, y, amp: v});
      }
    }
    return {spots: out, th, T};
  }

  // Where to score alignment. Two sources, and the mask is the union of whichever are usable:
  //  - static room texture (no LED light on it, but structure): cannot be biased by which LEDs
  //    are lit, so it is the honest estimator, and a warehouse is full of it;
  //  - the LED cores themselves, which is all there is in a dark room.
  // Also returns the band-passed all-on photo, which every frame is matched against.
  //
  // The mask is thinned to MASK_MAX points, evenly through the picture. This is the decoder's
  // main speed control and it is nearly free: on a real capture, going from 40,000 points to
  // 10,000 took lining up 29 photos from 4.2 s to 1.8 s and changed the result by one LED
  // (430 -> 431 found, both at 0.96). Alignment is averaging over tens of thousands of
  // samples for a few pixels of shift; it does not need all of them.
  const MASK_MAX = 10000;
  function lights(white, black, W, H, T) {
    const N = W * H, on = new Float32Array(N);
    for (let p = 0; p < N; p++) on[p] = white[p] - black[p];
    const found = findSpots(on, W, H, T), spots = found.spots;
    const ref = bandpass(white, W, H), bpBlack = bandpass(black, W, H);
    const bg = [];
    for (let y = SPOT_MARGIN; y < H - SPOT_MARGIN; y++)
      for (let x = SPOT_MARGIN; x < W - SPOT_MARGIN; x++) {
        const p = y * W + x;
        if (on[p] < 18 && Math.abs(bpBlack[p]) > 4) bg.push(p);
      }
    const core = [];
    for (const s of spots) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++)
      core.push((s.y + dy) * W + s.x + dx);
    // the room's texture is the unbiasable estimator, so prefer it; a dark room falls back to
    // the LED cores, and a thin result uses both
    let src = bg.length >= 5000 ? bg : core;
    if (bg.length >= 5000 && bg.length < 20000) src = bg.concat(core);
    const step = Math.max(1, Math.ceil(src.length / MASK_MAX));
    const pts = new Int32Array(Math.ceil(src.length / step));
    for (let i = 0, j = 0; i < src.length; i += step) pts[j++] = src[i];
    const qw = W >> 2, seen = new Set(), ptsQ = [];
    for (const p of pts) {
      const q = (((p / W) | 0) >> 2) * qw + ((p % W) >> 2);
      if (!seen.has(q)) { seen.add(q); ptsQ.push(q); }
    }
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (const s of spots) {
      if (s.x < x0) x0 = s.x; if (s.x > x1) x1 = s.x;
      if (s.y < y0) y0 = s.y; if (s.y > y1) y1 = s.y;
    }
    return {spots, pts, ptsQ: Int32Array.from(ptsQ), ref, on, th: found.th, W, H,
            count: spots.length, usingBackground: src !== core,
            box: x1 < 0 ? null : [x0, y0, x1, y1]};
  }

  // ------------------------------------------------------------------ lining the photos up
  function quarter(a, W, H) {
    const w = W >> 2, h = H >> 2, o = new Float32Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0;
      for (let dy = 0; dy < 4; dy++) { const r = (4 * y + dy) * W + 4 * x; s += a[r] + a[r + 1] + a[r + 2] + a[r + 3]; }
      o[y * w + x] = s / 16;
    }
    return {d: o, w, h};
  }

  // Mean |frame - reference| over the mask, at a whole-pixel offset. Lower is better.
  function sad(img, ref, W, H, pts, dx, dy, from, to) {
    let s = 0, n = 0;
    for (let i = from; i < to; i++) {
      const p = pts[i], x = (p % W) + dx, y = ((p / W) | 0) + dy;
      if (x < 0 || y < 0 || x >= W || y >= H) continue;
      const d = img[y * W + x] - ref[p];
      s += d < 0 ? -d : d; n++;
    }
    return n > 16 ? s / n : Infinity;
  }

  // Drift of `img` against the all-on reference: {dx, dy, z}. Coarse at quarter size, then
  // +-4 px at full size, then a parabola for the fraction of a pixel. z is how far the best
  // score stands out from the rest, in standard deviations.
  // Each photo is searched around where the PREVIOUS one landed, never over the whole range.
  // A hand drifts continuously -- measured on a real capture, ~4 px between consecutive photos,
  // 8 px at the 90th percentile, ~22 px end to end -- so a +-16 px window always contains the
  // answer while the total drift is free to accumulate. A wide search would be both slower and
  // wrong: a regularly wound string is a lattice, so a shift of exactly one LED pitch relabels
  // every LED as its neighbour and scores just as well. The first photo is searched around no
  // shift at all, because it is taken moments after the all-on photo it is matched against.
  //
  // One limit worth knowing: a coding photo lights half the LEDs and is matched against the
  // all-on photo, so any shift that maps the string's LEDs onto other LEDs scores exactly as
  // well as the true one. On a real draped string the LEDs are irregular enough that no such
  // shift exists, and in a lit room the match is made on the room's own texture, which settles
  // it outright. A perfectly regular grid of LEDs photographed in a blacked-out room is the one
  // case this cannot resolve; keeping the per-photo window small is what bounds the damage.
  const MAX_SHIFT = 12, STEP_SHIFT = 12;
  function shiftOf(img, W, H, L, maxShift, near) {
    maxShift = maxShift === undefined ? MAX_SHIFT : maxShift;
    const nx = near ? Math.round(near.dx) : 0, ny = near ? Math.round(near.dy) : 0;
    const cxq = Math.round(nx / 4), cyq = Math.round(ny / 4);
    const bp = bandpass(img, W, H);
    const q = quarter(bp, W, H), qr = quarter(L.ref, W, H), m = Math.ceil(maxShift / 4);
    const grid = [];
    let best = Infinity, bx = cxq, by = cyq;
    for (let dy = cyq - m; dy <= cyq + m; dy++) for (let dx = cxq - m; dx <= cxq + m; dx++) {
      let s = 0, n = 0;
      for (let i = 0; i < L.ptsQ.length; i++) {
        const p = L.ptsQ[i], x = (p % q.w) + dx, y = ((p / q.w) | 0) + dy;
        if (x < 0 || y < 0 || x >= q.w || y >= q.h) continue;
        const d = q.d[y * q.w + x] - qr.d[p];
        s += d < 0 ? -d : d; n++;
      }
      const v = n > 16 ? s / n : Infinity;
      if (isFinite(v)) grid.push([dx, dy, v]);
      if (v < best) { best = v; bx = dx; by = dy; }
    }
    // How clearly the match stands out: measured against the positions well away from it, not
    // against the whole grid. The scores right next to the best one are nearly as low whatever
    // the picture, so including them inflates the spread and makes a good match look doubtful
    // -- and how much they weigh depends on how wide the search was, which must not change the
    // verdict.
    const far = grid.filter(g => Math.abs(g[0] - bx) > 2 || Math.abs(g[1] - by) > 2).map(g => g[2]);
    const mean = far.reduce((a, b) => a + b, 0) / (far.length || 1);
    const sd = Math.sqrt(far.reduce((a, b) => a + (b - mean) ** 2, 0) / (far.length || 1)) || 1;
    const z = far.length >= 8 ? (mean - best) / sd : 0;
    const n = L.pts.length;
    const f = (dx, dy) => sad(bp, L.ref, W, H, L.pts, dx, dy, 0, n);
    let fb = Infinity, fx = 4 * bx, fy = 4 * by;
    fb = f(fx, fy);
    for (let dy = 4 * by - 4; dy <= 4 * by + 4; dy++) for (let dx = 4 * bx - 4; dx <= 4 * bx + 4; dx++) {
      const s = f(dx, dy);
      if (s < fb) { fb = s; fx = dx; fy = dy; }
    }
    const para = (a, b, c) => { const d = a - 2 * b + c; return d > 0 ? Math.max(-0.5, Math.min(0.5, (a - c) / (2 * d))) : 0; };
    const sx = para(f(fx - 1, fy), fb, f(fx + 1, fy)), sy = para(f(fx, fy - 1), fb, f(fx, fy + 1));
    return {dx: fx + sx, dy: fy + sy, z: Math.round(z * 10) / 10, bp};
  }

  // A hand aimed up at a prop rolls as well as drifting, so the shift is not the same across
  // the picture. Re-measure it in a 4x6 grid of blocks round the global shift and fit
  // dx, dy = a*x + b*y + c to the blocks that gave a clear answer. Measured on a real capture:
  // halves the residual mid-capture and takes the yield from ~240 to ~425 of 1250 LEDs.
  const GRID_Y = 4, GRID_X = 6, BLOCK_R = 6, BLOCK_MIN_Z = 1.2;
  function affineOf(bp, W, H, L, dx0, dy0) {
    const rows = [];
    const order = Array.from(L.pts).sort((a, b) => a - b);
    for (let by = 0; by < GRID_Y; by++) for (let bx = 0; bx < GRID_X; bx++) {
      const y0 = (by * H / GRID_Y) | 0, y1 = ((by + 1) * H / GRID_Y) | 0;
      const x0 = (bx * W / GRID_X) | 0, x1 = ((bx + 1) * W / GRID_X) | 0;
      const sel = [];
      for (const p of order) {
        const x = p % W, y = (p / W) | 0;
        if (x >= x0 && x < x1 && y >= y0 && y < y1) sel.push(p);
      }
      if (sel.length < 200) continue;
      const pts = Int32Array.from(sel);
      const ix = Math.round(dx0), iy = Math.round(dy0), vals = [];
      let best = Infinity, bdx = ix, bdy = iy;
      for (let dy = iy - BLOCK_R; dy <= iy + BLOCK_R; dy++) for (let dx = ix - BLOCK_R; dx <= ix + BLOCK_R; dx++) {
        const v = sad(bp, L.ref, W, H, pts, dx, dy, 0, pts.length);
        if (isFinite(v)) vals.push(v);
        if (v < best) { best = v; bdx = dx; bdy = dy; }
      }
      if (vals.length < 9) continue;
      const mu = vals.reduce((a, b) => a + b, 0) / vals.length;
      const sd = Math.sqrt(vals.reduce((a, b) => a + (b - mu) ** 2, 0) / vals.length) || 1;
      const z = (mu - best) / sd;
      if (z < BLOCK_MIN_Z) continue;
      rows.push([(x0 + x1) / 2, (y0 + y1) / 2, bdx, bdy, z]);
    }
    if (rows.length < 8) return null;
    const solve = (use, col) => {                       // weighted least squares for [a, b, c]
      const A = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], v = [0, 0, 0];
      for (const r of use) {
        const g = [r[0], r[1], 1], w = r[4];
        for (let i = 0; i < 3; i++) {
          for (let j = 0; j < 3; j++) A[i][j] += w * g[i] * g[j];
          v[i] += w * g[i] * r[col];
        }
      }
      for (let i = 0; i < 3; i++) A[i][i] += 1e-6;
      for (let i = 0; i < 3; i++) {                     // Gaussian elimination, 3x3
        let piv = i;
        for (let k = i + 1; k < 3; k++) if (Math.abs(A[k][i]) > Math.abs(A[piv][i])) piv = k;
        if (Math.abs(A[piv][i]) < 1e-12) return null;
        const ta = A[i]; A[i] = A[piv]; A[piv] = ta;
        const tv = v[i]; v[i] = v[piv]; v[piv] = tv;
        for (let k = i + 1; k < 3; k++) {
          const fq = A[k][i] / A[i][i];
          for (let j = i; j < 3; j++) A[k][j] -= fq * A[i][j];
          v[k] -= fq * v[i];
        }
      }
      const out = [0, 0, 0];
      for (let i = 2; i >= 0; i--) {
        let t = v[i];
        for (let j = i + 1; j < 3; j++) t -= A[i][j] * out[j];
        out[i] = t / A[i][i];
      }
      return out;
    };
    // fit, drop the blocks that disagree most, fit again: one bad block (a moving person, a
    // reflection) must not tilt the whole frame
    let use = rows, cx = null, cy = null;
    for (let pass = 0; pass < 2; pass++) {
      cx = solve(use, 2); cy = solve(use, 3);
      if (!cx || !cy) return null;
      if (pass) break;
      const res = use.map(r => Math.hypot(cx[0] * r[0] + cx[1] * r[1] + cx[2] - r[2],
                                          cy[0] * r[0] + cy[1] * r[1] + cy[2] - r[3]));
      const sorted = res.slice().sort((a, b) => a - b), med = sorted[sorted.length >> 1];
      const keep = use.filter((r, i) => res[i] <= Math.max(1.5, 2.5 * med));
      if (keep.length >= 8 && keep.length < use.length) use = keep;
    }
    // a hand rolls and leans; it does not shear the picture by 2%. Anything bigger is a bad fit.
    const lin = Math.max(Math.abs(cx[0]), Math.abs(cx[1]), Math.abs(cy[0]), Math.abs(cy[1]));
    if (!(lin < 0.02)) return null;
    let worst = 0;
    for (const r of use)
      worst = Math.max(worst, Math.hypot(cx[0] * r[0] + cx[1] * r[1] + cx[2] - r[2],
                                         cy[0] * r[0] + cy[1] * r[1] + cy[2] - r[3]));
    return {cx, cy, blocks: use.length, worst};
  }

  // `a` sampled at the affine-mapped position: undoes the measured drift, roll and zoom.
  function warped(a, W, H, M) {
    const o = new Uint8ClampedArray(W * H);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const sx = x + M.cx[0] * x + M.cx[1] * y + M.cx[2];
      const sy = y + M.cy[0] * x + M.cy[1] * y + M.cy[2];
      const x0 = Math.floor(sx), y0 = Math.floor(sy);
      if (x0 < 0 || y0 < 0 || x0 + 1 >= W || y0 + 1 >= H) continue;
      const fx = sx - x0, fy = sy - y0, r0 = y0 * W + x0, r1 = r0 + W;
      o[y * W + x] = (a[r0] * (1 - fx) + a[r0 + 1] * fx) * (1 - fy)
                   + (a[r1] * (1 - fx) + a[r1 + 1] * fx) * fy;
    }
    return o;
  }
  function translation(dx, dy) { return {cx: [0, 0, dx], cy: [0, 0, dy]}; }
  // `a` sampled at (x + dx, y + dy): undoes a drift of (dx, dy).
  function shifted(a, W, H, dx, dy) { return warped(a, W, H, translation(dx, dy)); }

  // Line every photo of a capture up with the all-on one. shots: {step: {W, H, lum, (r, g, b)}}.
  function stabilise(shots, opts) {
    const o = Object.assign({minZ: 1, maxShift: MAX_SHIFT, affine: true, order: null}, opts || {});
    const W = shots.white.W, H = shots.white.H;
    const L = lights(shots.white.lum, shots.black.lum, W, H);
    const out = {white: shots.white}, shifts = {white: [0, 0, 99]}, unaligned = [];
    // IN CAPTURE ORDER: each photo's drift is searched around the previous photo's, so the
    // order matters. Callers that hold the real order should pass it; otherwise the object's
    // own key order is used, which is the order the phone filled it in.
    const names = (o.order || Object.keys(shots)).filter(n => n !== 'white' && shots[n]);
    let last = {dx: 0, dy: 0}, rolls = 0, seeded = false;
    const est = {};
    for (const n of names) {
      if (n === 'black') continue;                     // nothing lit in it to line up with, and
                                                       // seeding the search from it locks the
                                                       // whole capture onto a wrong lattice fold
      const s = shiftOf(shots[n].lum, W, H, L, STEP_SHIFT, last);
      // the photo that seeds the search must be unambiguous: on a regularly wound string a
      // whole fold away scores nearly as well, and every later photo follows the seed
      const need = seeded ? o.minZ : Math.max(o.minZ, 3);
      if (s.z < need) { unaligned.push(n); est[n] = {dx: last.dx, dy: last.dy, z: s.z, bp: s.bp}; }
      else { est[n] = s; last = {dx: s.dx, dy: s.dy}; seeded = true; }
    }
    // the all-off photo was taken among the others: give it the nearest measured drift
    const near = names.find(n => n !== 'black' && est[n] && est[n].z >= o.minZ);
    est.black = {dx: near ? est[near].dx : 0, dy: near ? est[near].dy : 0, z: 0,
                 bp: bandpass(shots.black.lum, W, H)};
    let drift = 0;
    for (const n of names) {
      const e = est[n], sh = shots[n];
      let M = translation(e.dx, e.dy), kind = 'shift';
      if (o.affine) {
        const A = affineOf(e.bp, W, H, L, e.dx, e.dy);
        if (A && A.worst < 8) { M = A; kind = 'affine'; rolls++; }
      }
      shifts[n] = [Math.round(e.dx * 10) / 10, Math.round(e.dy * 10) / 10, e.z, kind];
      drift = Math.max(drift, Math.hypot(e.dx, e.dy));
      const c = {W, H, lum: warped(sh.lum, W, H, M)};
      if (sh.r) { c.r = warped(sh.r, W, H, M); c.g = warped(sh.g, W, H, M); c.b = warped(sh.b, W, H, M); }
      if (sh.chroma) c.chroma = warped(sh.chroma, W, H, M);
      out[n] = c;
    }
    return {shots: out, shifts, unaligned, affine: rolls, drift: Math.round(drift * 10) / 10,
            box: L.box, lit: L.count, spots: L.spots.length, background: L.usingBackground};
  }

  // ------------------------------------------------------------------ decoding
  // Each LED's code is read at its own spot, from the top-hat of each pattern and its inverse.
  // The bits are combined as a soft codeword rather than ANDed: only 1250 of the 2048 11-bit
  // words are real LEDs, so matching against all of them corrects a bit that came out marginal
  // instead of throwing the whole LED away.
  function decode(f, total, opts) {
    const o = Object.assign({minConfAbs: 6, minPixels: 2, spotT: SPOT_T}, opts || {});
    const W = f.W, H = f.H, N = W * H, nb = f.bits.length;
    const on = new Float32Array(N);
    for (let p = 0; p < N; p++) on[p] = f.white[p] - f.black[p];
    let litPixels = 0;
    for (let p = 0; p < N; p++) if (on[p] >= 20) litPixels++;
    const spots = findSpots(on, W, H, o.spotT).spots;
    const n = spots.length;
    if (!n) return empty(f, total, litPixels, on, o);
    const amps = spots.map(s => s.amp).sort((a, b) => a - b);
    const medAmp = Math.max(8, amps[n >> 1]);
    // per spot, per bit: how far the pattern beat its inverse at that spot's core
    const Lk = new Float32Array(n * nb), conf = new Float32Array(n).fill(Infinity);
    for (let k = 0; k < nb; k++) {
      const tb = tophat(f.bits[k][0], W, H), ti = tophat(f.bits[k][1], W, H);
      for (let i = 0; i < n; i++) {
        const p = spots[i].y * W + spots[i].x, d = tb[p] - ti[p];
        Lk[i * nb + k] = d;
        const a = d < 0 ? -d : d;
        if (a < conf[i]) conf[i] = a;
      }
    }
    // soft codeword match: tanh keeps a clean bit at +-1 and a marginal one soft
    const G = new Int8Array(total * nb);
    for (let g = 0; g < total; g++) {
      const code = grayCode(g + 1);
      for (let k = 0; k < nb; k++) G[g * nb + k] = (code >> k) & 1 ? 1 : -1;
    }
    const scale = 0.5 * medAmp;
    const X = new Float32Array(n * nb);
    for (let i = 0; i < n * nb; i++) X[i] = Math.tanh(Lk[i] / scale);
    const bestG = new Int32Array(n), margin = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let b1 = -Infinity, b2 = -Infinity, bg = -1;
      for (let g = 0; g < total; g++) {
        let s = 0;
        for (let k = 0; k < nb; k++) s += X[i * nb + k] * G[g * nb + k];
        if (s > b1) { b2 = b1; b1 = s; bg = g; } else if (s > b2) b2 = s;
      }
      bestG[i] = bg; margin[i] = b1 - b2;
    }
    // one spot per LED: the most confident
    const pts = new Array(total).fill(null), who = new Float32Array(total), spotOf = new Int32Array(total).fill(-1);
    let decoded = 0;
    for (let i = 0; i < n; i++) {
      if (conf[i] < o.minConfAbs) continue;
      decoded++;
      const g = bestG[i];
      if (g < 0 || g >= total) continue;
      if (spotOf[g] < 0 || conf[i] > who[g]) { who[g] = conf[i]; spotOf[g] = i; }
    }
    for (let g = 0; g < total; g++) if (spotOf[g] >= 0) pts[g] = [spots[spotOf[g]].x, spots[spotOf[g]].y];

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
    // how many neighbouring pairs sit a plausible LED pitch apart: the honest quality number,
    // where the median step alone can look right while the spread is nonsense
    let inBand = 0, pairs = 0;
    for (let g = 1; g < total; g++) if (pts[g] && pts[g - 1]) {
      const d = dist(pts[g], pts[g - 1]); pairs++;
      if (d >= 0.3 * m && d <= 1.9 * m) inBand++;
    }
    const colours = colourOrder(f, pts, total, W, H);
    const moved = movedBy(f, W, H, spots);
    const found = pts.reduce((c, p) => c + (p ? 1 : 0), 0);
    return {
      points: pts.map(p => p && [p[0] / W, p[1] / W]),   // normalised by width: x 0..1, y keeps the aspect
      pixels: pts, colours,
      stats: {total, found, outliers, litPixels, decoded, spots: n,
              moved: Math.round(moved * 100) / 100, cameraMoved: moved > 0.5,
              stepPx: Math.round(m * 10) / 10,
              inBand: pairs ? Math.round(100 * inBand / pairs) / 100 : 0},
    };
  }
  function grayCode(v) { return v ^ (v >> 1); }
  function empty(f, total, litPixels, on, o) {
    return {points: new Array(total).fill(null), pixels: new Array(total).fill(null),
            colours: new Array(total).fill(null),
            stats: {total, found: 0, outliers: 0, litPixels, decoded: 0, spots: 0,
                    moved: 0, cameraMoved: false, stepPx: 0, inBand: 0}};
  }
  // Did the camera move in a way the alignment could not undo? Compare the all-on photos from
  // the start and the end of the capture AFTER aligning them, band-passed and measured only at
  // the LED dots. Raw brightness would fail this on exposure drift alone: over 25 s the phone's
  // auto-white-balance shifts the whole frame, which is not the camera moving.
  function movedBy(f, W, H, spots) {
    if (!f.whiteEnd || spots.length < 8) return 0;
    const a = bandpass(f.white, W, H), b = bandpass(f.whiteEnd, W, H);
    let sa = 0, sb = 0, n = spots.length;
    for (const sp of spots) { const p = sp.y * W + sp.x; sa += a[p]; sb += b[p]; }
    const ma = sa / n, mb = sb / n;
    let cab = 0, caa = 0, cbb = 0;
    for (const sp of spots) {
      const p = sp.y * W + sp.x, da = a[p] - ma, db = b[p] - mb;
      cab += da * db; caa += da * da; cbb += db * db;
    }
    const ncc = caa > 0 && cbb > 0 ? cab / Math.sqrt(caa * cbb) : 0;
    return Math.max(0, 1 - ncc);                        // 0 = the two all-on photos still match
  }
  // colour order: which camera channel answered each wire channel, at each found LED
  function colourOrder(f, pts, total, W, H) {
    const colours = new Array(total).fill(null);
    if (!f.colour) return colours;
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
    return colours;
  }

  // How different two captures are where LEDs can be: mean |a - b| over the band-passed
  // picture at the LED spots, as a share of their amplitude. The phone uses it to catch a photo
  // taken before the LEDs had changed (camera lag), and takes it again. Band-passed, so a
  // whole-frame exposure shift between the two photos does not read as a change.
  function change(a, b, white, black) {
    let d = 0, s = 0;
    for (let p = 0; p < a.length; p += 3) {
      const o = white[p] - black[p];
      if (o < 20) continue;
      d += Math.abs(a[p] - b[p]); s += o;
    }
    return s ? d / s : 1;
  }
  // Band-passed version used when the caller knows W/H and the spot list.
  function changeAt(a, b, W, H, L) {
    const ba = bandpass(a, W, H), bb = bandpass(b, W, H);
    let d = 0, s = 0;
    for (const sp of L.spots) {
      const p = sp.y * W + sp.x;
      d += Math.abs(ba[p] - bb[p]); s += sp.amp;
    }
    return s ? d / s : 1;
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
    return s.z < 2 ? img : shifted(img, W, H, s.dx, s.dy);
  }

  // Decode a capture by brightness and by green-ness, and keep the better one. "Better" is not
  // simply more LEDs: a decode can find more by scattering them anywhere, so the count is
  // weighted by how many neighbouring pairs sit a plausible LED pitch apart.
  function decodeBest(A, bits, total, opts) {
    const make = key => {
      const f = {W: A.white.W, H: A.white.H, black: A.black[key], white: A.white[key], whiteEnd: A.white_end[key], bits: [],
                 colour: {black: A.black, red: A.red, green: A.green, blue: A.blue}};
      for (let k = 0; k < bits; k++) f.bits.push([A['b' + k][key], A['i' + k][key]]);
      return f;
    };
    const score = r => r.stats.found * Math.max(0.05, r.stats.inBand);
    const byLum = decode(make('lum'), total, opts);
    if (!A.white.chroma) return Object.assign(byLum, {signal: 'brightness', tried: {brightness: byLum.stats.found}});
    const byGreen = decode(make('chroma'), total, opts);
    const best = score(byGreen) > score(byLum) ? Object.assign(byGreen, {signal: 'green-ness'})
                                               : Object.assign(byLum, {signal: 'brightness'});
    best.tried = {brightness: byLum.stats.found, 'green-ness': byGreen.stats.found};
    return best;
  }

  const api = {decode, decodeBest, grayDecode, change, changeAt, stabilise, shiftOf, lights,
               alignedLum, greenness, findSpots, spotThreshold, tophat, bandpass, boxMean, warped, shifted};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MapDecode = api;
})(typeof window !== 'undefined' ? window : this);
