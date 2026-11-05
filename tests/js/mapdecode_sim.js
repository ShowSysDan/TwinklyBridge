// Synthetic camera for the mapping decoder: renders what a phone would photograph for each
// step the bridge lights, then decodes and prints a JSON summary for tests/test_mapping.py.
const MD = require(process.argv[2]);
const HARD = process.argv[3] === 'hard';      // bloom, clipping, banding, a floor reflection, more noise
const SHAKE = process.argv[3] === 'shake';    // a hand-held phone: the whole picture drifts up to 12 px per photo
const BRIGHT = process.argv[3] === 'bright';  // a lit warehouse: grey textured room ~140, copper tinsel that glints,
                                              // camera exposed for the room, green spill round every LED
const STAB = process.env.STAB !== '0';        // line the photos up before decoding (as the phone does)
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
function render(step) {      // -> {r, g, b, lum} as the camera sees it
  const jx = SHAKE && step !== 'white' ? (rnd() - 0.5) * 24 : 0, jy = SHAKE && step !== 'white' ? (rnd() - 0.5) * 24 : 0;
  const gain = HARD ? 0.8 + 0.4 * rnd() : 0.85 + 0.3 * rnd(), r = new Float32Array(W * H), gg = new Float32Array(W * H), b = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) { r[i] = gg[i] = b[i] = 10; }
  if (BRIGHT) for (let i = 0; i < W * H; i++) {
    const t = ROOM[i];                                      // static room texture, plus tinsel glints that change
    r[i] = t[0] * (1 + 0.08 * (rnd() - 0.5) * t[3]); gg[i] = t[1] * (1 + 0.08 * (rnd() - 0.5) * t[3]); b[i] = t[2];
  }
  for (let g = 0; g < N; g++) {
    if (!present(g)) continue;
    let rgb;
    if (step === 'red' || step === 'green' || step === 'blue') {
      const sentCh = ['red', 'green', 'blue'].indexOf(step);
      const seenLetter = order(g)[sentCh];                  // wire channel k drives this colour
      rgb = [seenLetter === 'R' ? 1 : 0, seenLetter === 'G' ? 1 : 0, seenLetter === 'B' ? 1 : 0];
    } else rgb = lit(g, step) ? [0, 1, 0] : null;          // coding frames: green full on
    if (!rgb) continue;
    const blobs = [[pos[g][0] + jx, pos[g][1] + jy, HARD ? 230 : BRIGHT ? 70 : 140, HARD ? 1.5 : 1.1]];
    if (BRIGHT) blobs.push([pos[g][0] + jx, pos[g][1] + jy, 22, 5]);       // green spill on the tinsel
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
  const nz = HARD || BRIGHT ? 16 : 8;
  for (let i = 0; i < W * H; i++) {
    const gn = gain * band[(i / W) | 0];
    R[i] = clamp(r[i] * gn + (rnd() - 0.5) * nz); G[i] = clamp(gg[i] * gn + (rnd() - 0.5) * nz);
    B[i] = clamp(b[i] * gn + (rnd() - 0.5) * nz); L[i] = clamp(0.299 * R[i] + 0.587 * G[i] + 0.114 * B[i]);
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
const st = STAB ? MD.stabilise(shots) : {shots, drift: 0, unaligned: [], box: null};
const A = st.shots;
const f = {W, H, black: A.black.lum, white: A.white.lum, whiteEnd: A.white_end.lum, bits: [],
           colour: {black: A.black, red: A.red, green: A.green, blue: A.blue}};
for (let k = 0; k < bits; k++) f.bits.push([A['b' + k].lum, A['i' + k].lum]);
const res = MD.decodeBest(A, bits, N);
let err = 0, n = 0, worst = 0, wrong = 0;
res.pixels.forEach((p, g) => { if (!p) return; const d = Math.hypot(p[0] - pos[g][0], p[1] - pos[g][1]); err += d; n++; worst = Math.max(worst, d); if (d > 3) wrong++; });
const colours = {};
res.colours.forEach((c, g) => { const k = Math.floor(g / PORT) + ':' + c; colours[k] = (colours[k] || 0) + 1; });
console.log(JSON.stringify({signal: res.signal, tried: res.tried, stab: {drift: st.drift, unaligned: st.unaligned, box: st.box}, stats: res.stats, meanErr: err / n, worst, wrong, colours,
  foundPerPort: [0, 1, 2, 3, 4].map(p => res.points.slice(p * PORT, (p + 1) * PORT).filter(Boolean).length),
  lastPort4: Math.max(...res.points.slice(4 * PORT).map((p, k) => (p ? k + 1 : 0))),
  points: res.points, colourList: res.colours}));
