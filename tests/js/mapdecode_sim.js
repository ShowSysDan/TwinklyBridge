// Synthetic camera for the mapping decoder: renders what a phone would photograph for each
// step the bridge lights, then decodes and prints a JSON summary for tests/test_mapping.py.
const MD = require(process.argv[2]);
const HARD = process.argv[3] === 'hard';      // bloom, clipping, banding, a floor reflection, more noise
const W = 640, H = 360, N = 1250, PORT = 250;
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
// five strings, each a gentle wave across the frame, 4 px apart along the string
const pos = [];
for (let p = 0; p < 5; p++) for (let k = 0; k < PORT; k++)
  pos.push([40 + k * 2.2, 50 + p * 60 + 12 * Math.sin(k / 9) + k * 0.05]);
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
function render(step) {      // -> {r, g, b, lum} as the camera sees it
  const gain = HARD ? 0.8 + 0.4 * rnd() : 0.85 + 0.3 * rnd(), r = new Float32Array(W * H), gg = new Float32Array(W * H), b = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) { r[i] = gg[i] = b[i] = 10; }
  for (let g = 0; g < N; g++) {
    if (!present(g)) continue;
    let rgb;
    if (step === 'red' || step === 'green' || step === 'blue') {
      const sentCh = ['red', 'green', 'blue'].indexOf(step);
      const seenLetter = order(g)[sentCh];                  // wire channel k drives this colour
      rgb = [seenLetter === 'R' ? 1 : 0, seenLetter === 'G' ? 1 : 0, seenLetter === 'B' ? 1 : 0];
    } else rgb = lit(g, step) ? [0, 1, 0] : null;          // coding frames: green full on
    if (!rgb) continue;
    const blobs = [[pos[g][0], pos[g][1], HARD ? 230 : 140, HARD ? 1.5 : 1.1]];
    if (HARD) { blobs.push([pos[g][0], pos[g][1], 25, 4]);                     // bloom halo
                blobs.push([pos[g][0] + 6, 2 * 340 - pos[g][1], 45, 2.5]); }   // reflection off a shiny floor
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
  const nz = HARD ? 16 : 8;
  for (let i = 0; i < W * H; i++) {
    const gn = gain * band[(i / W) | 0];
    R[i] = clamp(r[i] * gn + (rnd() - 0.5) * nz); G[i] = clamp(gg[i] * gn + (rnd() - 0.5) * nz);
    B[i] = clamp(b[i] * gn + (rnd() - 0.5) * nz); L[i] = clamp(0.299 * R[i] + 0.587 * G[i] + 0.114 * B[i]);
  }
  return {r: R, g: G, b: B, lum: L};
}
const black = render('black');
const f = {W, H, black: black.lum, white: render('white').lum, whiteEnd: render('white_end').lum, bits: [],
           colour: {black, red: render('red'), green: render('green'), blue: render('blue')}};
for (let k = 0; k < bits; k++) f.bits.push([render('b' + k).lum, render('i' + k).lum]);
const res = MD.decode(f, N);
let err = 0, n = 0, worst = 0, wrong = 0;
res.pixels.forEach((p, g) => { if (!p) return; const d = Math.hypot(p[0] - pos[g][0], p[1] - pos[g][1]); err += d; n++; worst = Math.max(worst, d); if (d > 3) wrong++; });
const colours = {};
res.colours.forEach((c, g) => { const k = Math.floor(g / PORT) + ':' + c; colours[k] = (colours[k] || 0) + 1; });
console.log(JSON.stringify({stats: res.stats, meanErr: err / n, worst, wrong, colours,
  foundPerPort: [0, 1, 2, 3, 4].map(p => res.points.slice(p * PORT, (p + 1) * PORT).filter(Boolean).length),
  lastPort4: Math.max(...res.points.slice(4 * PORT).map((p, k) => (p ? k + 1 : 0))),
  points: res.points, colourList: res.colours}));
