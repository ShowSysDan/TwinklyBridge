// Synthetic camera for the mapping decoder: renders what a phone would photograph for each
// step the bridge lights, then decodes and prints a JSON summary for tests/test_mapping.py.
//
// The geometry and the light here are taken from a measured real capture (an iPhone, a tinsel
// star and mini trees in a lit warehouse, 1250 LEDs). Two properties matter and an earlier
// version of this file had neither, which is why the suite passed while real captures failed:
//
//   * SPACING. LEDs sit ~27 px apart along the wire and ~5 px from the nearest LED on the
//     neighbouring fold of the string -- not 2.2 px apart in a line. At 2.2 px any
//     "find the dots first" design looks like it merges neighbours, which is false on real data.
//   * SCATTERED GLOW. Tinsel diffuses light, so most of the raw brightness at an LED's position
//     is the OTHER lit LEDs' light coming back off the prop: measured, an LED supplies only
//     ~27% of the raw level at its own core. The decoder must survive that pedestal, which it
//     does by band-passing (the pedestal is smooth, an LED is a 3 px dot) -- but only a renderer
//     that produces the pedestal can test it.
const MD = require(process.argv[2]);
// Modes combine: "brightshake" is a lit warehouse photographed by a hand-held phone, which is
// the real-world case the user actually has.
const MODE = process.argv[3] || 'normal';
const HARD = MODE.includes('hard');      // bloom, clipping, banding, a floor reflection, more noise
const SHAKE = MODE.includes('shake');    // a hand-held phone: the picture wanders ~22 px and rolls
const BRIGHT = MODE.includes('bright');  // a lit warehouse: grey textured room ~140, copper tinsel that glints,
                                         // camera exposed for the room, heavy green spill round every LED
const STAB = process.env.STAB !== '0';        // line the photos up before decoding (as the phone does)
const W = 640, H = 360, N = 1250, PORT = 250;
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
// Five strings, each wound back and forth over its own patch of the prop: 25 LEDs per fold,
// 23 px apart along the wire, folds 5 px apart. So the nearest OTHER LED is ~5 px away while
// the next LED on the wire is ~23 px away, as measured on the real capture.
// A draped string is not a lattice: each LED is jittered a couple of pixels, which reproduces
// the measured spread of nearest-neighbour distances (p10 ~4 px, median ~8 px, p90 ~15 px) and
// is also what makes the picture registrable at all -- a perfectly periodic grid would have
// shifts that map every LED onto another LED and so cannot be lined up from the LEDs alone.
const pos = [], PITCH = 20, FOLD = 30, ROW = 7;
for (let p = 0; p < 5; p++) for (let k = 0; k < PORT; k++) {
  const row = Math.floor(k / FOLD), col = row % 2 ? FOLD - 1 - (k % FOLD) : k % FOLD;
  pos.push([25 + col * PITCH + 2 * Math.sin(k / 7) + (rnd() - 0.5) * 5,
            32 + p * 66 + row * ROW + 1.5 * Math.cos(k / 5) + (rnd() - 0.5) * 5]);
}
const present = g => !(g >= 4 * PORT + 200);               // port 4's string ends at LED 200
const order = g => (Math.floor(g / PORT) === 2 ? 'GRB' : 'RGB'); // port 2 wired GRB
const gray = v => v ^ (v >> 1);
const bits = Math.max(1, N.toString(2).length);
function lit(g, step) {
  if (step === 'white' || step === 'white_end') return true;
  if (step === 'black') return false;
  const b = (gray(g + 1) >> +step.slice(1)) & 1;
  return step[0] === 'b' ? !!b : !b;
}
// the warehouse: grey block wall with mortar lines and noise; copper tinsel where the strings are
const ROOM = [];
if (BRIGHT) {
  let sd = 3; const r2 = () => (sd = (sd * 48271) % 2147483647) / 2147483647;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const mortar = (y % 40 < 3 || (x + (Math.floor(y / 40) % 2) * 50) % 100 < 3) ? -25 : 0;
    const base = 140 + mortar + 20 * (r2() - 0.5);
    ROOM.push([base, base, base * 0.97, 0]);
  }
  for (const [x0, y0] of pos) for (let k = 0; k < 6; k++) {          // tinsel speckle near the strings
    const x = Math.round(x0 + (r2() - 0.5) * 14), y = Math.round(y0 + (r2() - 0.5) * 14);
    if (x < 0 || y < 0 || x >= W || y >= H) continue;
    ROOM[y * W + x] = [200, 130, 85, 1];
  }
}
// The glow every lit LED throws back off the tinsel: a broad, smooth field. Rendered by
// dropping each lit LED into a coarse grid and blurring it, which is both what the physics
// does and fast enough to run for every frame.
const GW = W >> 3, GH = H >> 3;
function blur1(a, w, h, r) {
  const o = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let s = 0, n = 0;
    for (let d = -r; d <= r; d++) { const q = x + d; if (q >= 0 && q < w) { s += a[y * w + q]; n++; } }
    o[y * w + x] = s / n;
  }
  const t = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let s = 0, n = 0;
    for (let d = -r; d <= r; d++) { const q = y + d; if (q >= 0 && q < h) { s += o[q * w + x]; n++; } }
    t[y * w + x] = s / n;
  }
  return t;
}
function glowField(isLit) {
  const g = new Float32Array(GW * GH);
  for (let i = 0; i < N; i++) {
    if (!present(i) || !isLit(i)) continue;
    const x = Math.min(GW - 1, Math.max(0, Math.round(pos[i][0] / 8)));
    const y = Math.min(GH - 1, Math.max(0, Math.round(pos[i][1] / 8)));
    g[y * GW + x] += 1;
  }
  return blur1(blur1(g, GW, GH, 3), GW, GH, 3);
}
function sampleGlow(f, x, y) {                       // bilinear, coarse grid -> full size
  const gx = Math.min(GW - 1.001, Math.max(0, x / 8)), gy = Math.min(GH - 1.001, Math.max(0, y / 8));
  const x0 = gx | 0, y0 = gy | 0, fx = gx - x0, fy = gy - y0;
  return (f[y0 * GW + x0] * (1 - fx) + f[y0 * GW + x0 + 1] * fx) * (1 - fy)
       + (f[(y0 + 1) * GW + x0] * (1 - fx) + f[(y0 + 1) * GW + x0 + 1] * fx) * fy;
}
const CORE = HARD ? 180 : BRIGHT ? 30 : 140;
// scale the glow so that, with every LED on, an LED's own core is the measured ~27% of the raw
// level there (so the pedestal is ~2.7x the core) in the lit-warehouse case, less otherwise
// 2.33 puts an LED's own light at the measured ~30% of the raw level at its own core
const PED = BRIGHT ? 2.33 : HARD ? 0.6 : 0.5;
const allOn = glowField(() => true);
let gsum = 0, gn = 0;
for (let i = 0; i < N; i += 7) if (present(i)) { gsum += sampleGlow(allOn, pos[i][0], pos[i][1]); gn++; }
const GLOW = gn && gsum > 0 ? PED * CORE / (gsum / gn) : 0;
// A hand-held phone does not jump about at random between photos: it wanders. Measured on a
// real capture, the picture moves ~4 px between consecutive photos (8 px at the 90th
// percentile) and ends ~22 px from where it started, along a smooth trajectory, with a few
// tenths of a degree of roll from aiming up at the prop.
let dx = 0, dy = 0;
function render(step) {      // -> {r, g, b, lum} as the camera sees it
  const shake = SHAKE && step !== 'white';
  if (shake) {
    dx = Math.max(-22, Math.min(22, dx + (rnd() - 0.5) * 9));
    dy = Math.max(-22, Math.min(22, dy + (rnd() - 0.5) * 9));
  }
  const jx = shake ? dx : 0, jy = shake ? dy : 0;
  if (process.env.TRUTH) console.error('TRUE', step, jx.toFixed(1), jy.toFixed(1));
  const roll = shake ? (rnd() - 0.5) * 0.016 : 0;        // +-0.46 deg: a hand aimed up at a prop
  // World -> screen for anything physically out there, and its inverse for asking what the
  // camera sees at a given screen pixel. The ROOM is part of the world: when the phone moves,
  // the wall behind the prop moves across the picture with it. Painting the room at fixed
  // screen positions while only the LEDs move is not a camera -- it is a camera bolted to the
  // wall with the prop sliding about, and it makes registration on room texture look broken
  // when it is working perfectly.
  const toScreen = (X, Y) => [X + jx + roll * (Y - H / 2), Y + jy - roll * (X - W / 2)];
  const toWorld = (x, y) => [x - jx - roll * (y - H / 2), y - jy + roll * (x - W / 2)];
  const gain = HARD ? 0.8 + 0.4 * rnd() : 0.85 + 0.3 * rnd();
  const r = new Float32Array(W * H), gg = new Float32Array(W * H), b = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) { r[i] = gg[i] = b[i] = 10; }
  if (BRIGHT) for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const w = toWorld(x, y);                                // the wall, seen from where the phone is
    const wx = Math.min(W - 1, Math.max(0, Math.round(w[0]))), wy = Math.min(H - 1, Math.max(0, Math.round(w[1])));
    const t = ROOM[wy * W + wx], p = y * W + x;             // static room texture, plus tinsel glints that change
    r[p] = t[0] * (1 + 0.08 * (rnd() - 0.5) * t[3]); gg[p] = t[1] * (1 + 0.08 * (rnd() - 0.5) * t[3]); b[p] = t[2];
  }
  const colourStep = step === 'red' || step === 'green' || step === 'blue';
  // the pedestal this frame's lit set throws onto the whole prop
  if (GLOW > 0 && step !== 'black') {
    const f = glowField(g => lit(g, step) || colourStep);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const w = toWorld(x, y), v = GLOW * sampleGlow(f, w[0], w[1]);
      if (v <= 0.01) continue;
      const p = y * W + x;
      if (colourStep) { r[p] += v * 0.33; gg[p] += v * 0.33; b[p] += v * 0.33; } else gg[p] += v;
    }
  }
  for (let g = 0; g < N; g++) {
    if (!present(g)) continue;
    let rgb;
    if (colourStep) {
      const sentCh = ['red', 'green', 'blue'].indexOf(step);
      const seenLetter = order(g)[sentCh];                  // wire channel k drives this colour
      rgb = [seenLetter === 'R' ? 1 : 0, seenLetter === 'G' ? 1 : 0, seenLetter === 'B' ? 1 : 0];
    } else rgb = lit(g, step) ? [0, 1, 0] : null;          // coding frames: green full on
    if (!rgb) continue;
    const [px, py] = toScreen(pos[g][0], pos[g][1]);
    const blobs = [[px, py, CORE, HARD ? 1.5 : 1.1]];
    if (BRIGHT) blobs.push([px, py, 10, 4]);            // close-in spill on the tinsel round each LED
    if (HARD) { blobs.push([px, py, 25, 4]);                                   // bloom halo
                blobs.push([px + 6, 2 * 340 - py, 45, 2.5]); }                 // reflection off a shiny floor
    for (const [x0, y0, amp, sig] of blobs) {
      const rr = Math.ceil(3 * sig);
      for (let y = Math.floor(y0 - rr); y <= y0 + rr; y++) for (let x = Math.floor(x0 - rr); x <= x0 + rr; x++) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        const a = amp * Math.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * sig ** 2)), p = y * W + x;
        r[p] += a * rgb[0]; gg[p] += a * rgb[1]; b[p] += a * rgb[2];
      }
    }
  }
  const clamp = v => Math.max(0, Math.min(255, Math.round(v)));
  const R = new Uint8ClampedArray(W * H), G = new Uint8ClampedArray(W * H), B = new Uint8ClampedArray(W * H), L = new Uint8ClampedArray(W * H);
  const band = new Float32Array(H).fill(1);               // stripes across the picture
  if (HARD) { const ph = rnd() * 6.28; for (let y = 0; y < H; y++) band[y] = 1 + 0.15 * Math.sin(y / 9 + ph); }
  const nz = HARD || BRIGHT ? 16 : 8;
  for (let i = 0; i < W * H; i++) {
    const gn2 = gain * band[(i / W) | 0];
    R[i] = clamp(r[i] * gn2 + (rnd() - 0.5) * nz); G[i] = clamp(gg[i] * gn2 + (rnd() - 0.5) * nz);
    B[i] = clamp(b[i] * gn2 + (rnd() - 0.5) * nz); L[i] = clamp(0.299 * R[i] + 0.587 * G[i] + 0.114 * B[i]);
  }
  const C = new Uint8ClampedArray(W * H);
  for (let i = 0; i < W * H; i++) C[i] = MD.greenness(R[i], G[i], B[i]);
  return {r: R, g: G, b: B, lum: L, chroma: C};
}
const shots = {white: render('white'), black: render('black')};
for (const c of ['red', 'green', 'blue']) shots[c] = render(c);
for (let k = 0; k < bits; k++) { shots['b' + k] = render('b' + k); shots['i' + k] = render('i' + k); }
shots.white_end = render('white_end');
for (const k in shots) { shots[k].W = W; shots[k].H = H; }
const st = STAB ? MD.stabilise(shots) : {shots, drift: 0, unaligned: [], box: null, affine: 0};
const A = st.shots;
const res = MD.decodeBest(A, bits, N);
let err = 0, n = 0, worst = 0, wrong = 0;
res.pixels.forEach((p, g) => { if (!p) return; const d = Math.hypot(p[0] - pos[g][0], p[1] - pos[g][1]); err += d; n++; worst = Math.max(worst, d); if (d > 4) wrong++; });
const colours = {};
res.colours.forEach((c, g) => { const k = Math.floor(g / PORT) + ':' + c; colours[k] = (colours[k] || 0) + 1; });
console.log(JSON.stringify({signal: res.signal, tried: res.tried,
  stab: {drift: st.drift, unaligned: st.unaligned, box: st.box, affine: st.affine},
  stats: res.stats, meanErr: n ? err / n : 99, worst, wrong, colours,
  foundPerPort: [0, 1, 2, 3, 4].map(p => res.points.slice(p * PORT, (p + 1) * PORT).filter(Boolean).length),
  lastPort4: Math.max(...res.points.slice(4 * PORT).map((p, k) => (p ? k + 1 : 0))),
  points: res.points, colourList: res.colours}));
