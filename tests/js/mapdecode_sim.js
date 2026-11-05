// Synthetic camera for the mapping decoder: renders what a phone would photograph for each
// step the bridge lights, then decodes and prints a JSON summary for tests/test_mapping.py.
const MD = require(process.argv[2]);
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
  const gain = 0.85 + 0.3 * rnd(), r = new Float32Array(W * H), gg = new Float32Array(W * H), b = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) { r[i] = gg[i] = b[i] = 10; }
  for (let g = 0; g < N; g++) {
    if (!present(g)) continue;
    let rgb;
    if (step === 'red' || step === 'green' || step === 'blue') {
      const sentCh = ['red', 'green', 'blue'].indexOf(step);
      const seenLetter = order(g)[sentCh];                  // wire channel k drives this colour
      rgb = [seenLetter === 'R' ? 1 : 0, seenLetter === 'G' ? 1 : 0, seenLetter === 'B' ? 1 : 0];
    } else rgb = lit(g, step) ? [1, 1, 1] : null;
    if (!rgb) continue;
    const [x0, y0] = pos[g];
    for (let y = Math.floor(y0 - 4); y <= y0 + 4; y++) for (let x = Math.floor(x0 - 4); x <= x0 + 4; x++) {
      if (x < 0 || y < 0 || x >= W || y >= H) continue;
      const a = 140 * Math.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * 1.1 ** 2)), p = y * W + x;
      r[p] += a * rgb[0]; gg[p] += a * rgb[1]; b[p] += a * rgb[2];
    }
  }
  const clamp = v => Math.max(0, Math.min(255, Math.round(v)));
  const R = new Uint8ClampedArray(W * H), G = new Uint8ClampedArray(W * H), B = new Uint8ClampedArray(W * H), L = new Uint8ClampedArray(W * H);
  for (let i = 0; i < W * H; i++) {
    R[i] = clamp(r[i] * gain + (rnd() - 0.5) * 8); G[i] = clamp(gg[i] * gain + (rnd() - 0.5) * 8);
    B[i] = clamp(b[i] * gain + (rnd() - 0.5) * 8); L[i] = clamp(0.299 * R[i] + 0.587 * G[i] + 0.114 * B[i]);
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
