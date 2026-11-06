// Slide one photo by a known whole number of pixels and check the decoder finds that shift
// again, for every offset. The coarse search used to step 4 px, so offsets of 2 and 6 px -
// the ones furthest from its step - came back about 9 px wrong while every other offset was
// exact. A mod-4 failure is invisible to any test that only tries one drift.
const MD = require(process.argv[2]);
const W = 640, H = 360, N = 900;
let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const pos = [];
// a wound string, like a real one: folds 7 px apart, 20 px along the wire, slightly irregular
for (let p = 0; p < 5; p++) for (let k = 0; k < N / 5; k++) {
  const row = Math.floor(k / 30), col = row % 2 ? 29 - (k % 30) : k % 30;
  pos.push([25 + col * 20 + (rnd() - 0.5) * 5, 30 + p * 66 + row * 7 + (rnd() - 0.5) * 5]);
}
function render(lit, dx, dy) {
  const a = new Float32Array(W * H).fill(8);
  for (let i = 0; i < N; i++) {
    if (!lit(i)) continue;
    const x0 = pos[i][0] + dx, y0 = pos[i][1] + dy, sig = 1.3;
    for (let y = Math.floor(y0 - 4); y <= y0 + 4; y++) for (let x = Math.floor(x0 - 4); x <= x0 + 4; x++) {
      if (x < 0 || y < 0 || x >= W || y >= H) continue;
      a[y * W + x] += 150 * Math.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * sig * sig));
    }
  }
  const o = new Uint8ClampedArray(W * H);
  for (let p = 0; p < o.length; p++) o[p] = a[p] + (rnd() - 0.5) * 6;
  return o;
}
const white = render(() => true, 0, 0), black = render(() => false, 0, 0);
const L = MD.lights(white, black, W, H);
const rows = [];
let worst = 0;
for (let ex = 0; ex <= 12; ex++) {
  const img = render(i => i % 2 === 0, ex, 0);            // half lit, like a coding pattern
  const s = MD.shiftOf(img, W, H, L, 16, {dx: 0, dy: 0});
  const err = Math.hypot(s.dx - ex, s.dy);
  worst = Math.max(worst, err);
  rows.push({offset: ex, dx: Math.round(s.dx * 100) / 100, dy: Math.round(s.dy * 100) / 100,
             err: Math.round(err * 100) / 100, z: s.z});
}
const zs = rows.map(r => r.z);
console.log(JSON.stringify({worst_error_px: Math.round(worst * 100) / 100,
  min_z: Math.min(...zs), max_z: Math.max(...zs),
  z_evenness: Math.round(100 * Math.min(...zs) / Math.max(...zs)) / 100, rows}));
