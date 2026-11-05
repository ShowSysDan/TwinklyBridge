// Phone page for camera mapping: opens the rear camera, steps the bridge through its
// Gray-coded frames, photographs each, decodes (mapdecode.js), keeps each capture as a view
// (with its angle around the object, from the compass), builds a 2D or 3D map from the
// views, saves it, and runs sweeps placed by the map to check it lines up.
'use strict';
const $ = s => document.querySelector(s);
const HTTPS_PORT = +$('#mappingScript').dataset.httpsPort || 0;
const MAX_W = 960;                    // analysis width: plenty for LEDs a few pixels apart
const SECTIONS = ['insecure', 'setup', 'cam', 'result', 'kept', 'built', 'check'];
let stream = null, session = null, decoded = null, wake = null, cancelled = false, lastReport = null;
let views = null, built = null, mirror = false;
const sensor = {heading: null, pitch: null, rot: 0, maxRot: 0, ok: false};

function toast(msg, ms) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), ms || (msg.length > 80 ? 9000 : 3000));
}
async function api(path, body, method) {
  const opts = body === undefined && !method ? {} : {method: method || 'POST', headers: {'Content-Type': 'application/json'},
                                                     body: body === undefined ? undefined : JSON.stringify(body)};
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `${path}: HTTP ${r.status}`);
  return j;
}
function esc(s) { return String(s).replace(/[&<>"]/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[ch])); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
function show(id) { for (const s of SECTIONS) $('#' + s).hidden = s !== id; window.scrollTo(0, 0); }

// ---------------------------------------------------------------- compass, tilt, steadiness
// The compass heading changes by the angle you walk round the object (facing it all along),
// so each view's angle = first view's heading - this heading. Tilt: 0 = phone upright.
function onOrientation(e) {
  const h = e.webkitCompassHeading !== undefined ? e.webkitCompassHeading
          : (e.absolute && e.alpha !== null ? (360 - e.alpha) % 360 : null);
  if (h !== null && h !== undefined) sensor.heading = h;
  if (e.beta !== null) sensor.pitch = e.beta - 90;
  sensor.ok = true;
  showSensors();
}
function onMotion(e) {
  const r = e.rotationRate;
  if (!r) return;
  sensor.rot = Math.hypot(r.alpha || 0, r.beta || 0, r.gamma || 0);
  sensor.maxRot = Math.max(sensor.maxRot, sensor.rot);
}
async function startSensors() {
  try {
    for (const E of [window.DeviceOrientationEvent, window.DeviceMotionEvent])
      if (E && typeof E.requestPermission === 'function') await E.requestPermission();
  } catch {}
  window.addEventListener('deviceorientationabsolute', onOrientation);
  window.addEventListener('deviceorientation', onOrientation);
  window.addEventListener('devicemotion', onMotion);
}
function compassAngle() {
  const first = views && views.views.length && views.views[0].heading;
  if (sensor.heading === null || first === null || first === undefined) return null;
  return Math.round(((first - sensor.heading) % 360 + 360) % 360);
}
let angleTouched = false;
function showSensors() {
  const parts = [];
  if (sensor.heading !== null) {
    const a = compassAngle();
    parts.push(a === null ? `compass ${Math.round(sensor.heading)}&deg; (first view sets 0)` : `compass says ${a}&deg; around`);
    if (a !== null && !angleTouched) $('#angle').value = a;
  } else parts.push('no compass');
  if (sensor.pitch !== null) {
    const t = Math.round(sensor.pitch);
    parts.push(Math.abs(t) > 15 ? `<strong class="bad">tilted ${t}&deg;: hold the phone upright</strong>` : `tilt ${t}&deg;`);
  }
  parts.push(sensor.rot > 15 ? '<strong class="bad">moving</strong>' : 'steady');
  $('#sensors').innerHTML = parts.join(' &middot; ');
}

// ---------------------------------------------------------------- setup
async function loadViews() {
  views = await api('/api/mapping/views');
  const box = $('#viewsBox');
  if (!views || !views.views.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="viewsum"><strong>${views.views.length} view${views.views.length > 1 ? 's' : ''} kept</strong>
      (${views.views.map(v => `${Math.round(v.angle)}&deg;`).join(', ')}), ${views.seen_any} of ${views.total} LEDs seen.
      <div class="rowbtns"><button id="buildNow">Build map</button><button id="discard" class="quiet">Discard views</button></div></div>`;
  $('#buildNow').onclick = () => build();
  $('#discard').onclick = async () => {
    if (!confirm('Discard the kept views?')) return;
    await api('/api/mapping/views', undefined, 'DELETE'); await loadViews();
  };
}
async function init() {
  if (!window.isSecureContext || !navigator.mediaDevices) {
    const host = location.hostname;
    $('#httpsLink').innerHTML = HTTPS_PORT
      ? `<a class="big primary button" href="https://${esc(host)}:${HTTPS_PORT}/map">https://${esc(host)}:${HTTPS_PORT}/map</a>`
      : 'HTTPS is not set up on the bridge (no certificate). Re-run the installer, or see the README, section "Camera mapping".';
    show('insecure');
    return;
  }
  const st = await api('/api/status');
  await loadViews();
  const inViews = new Set(views ? views.controllers : []);
  $('#ctrlList').innerHTML = st.controllers.length ? st.controllers.map(c =>
    `<label><input type="checkbox" value="${esc(c.id)}" ${(views ? inViews.has(c.id) : c.enabled) ? 'checked' : ''}>
       <span><strong>${esc(c.name)}</strong> ${esc(c.ip)}<br><span class="hint">${c.n_led} LEDs, ${c.status}${c.has_map ? `, map: ${esc(c.map_source)}` : ', no map'}</span></span></label>`).join('')
    : '<p class="hint">No controllers on the bridge yet. Add them on the main page first.</p>';
  show('setup');
}
$('#level').oninput = () => { $('#levelV').textContent = $('#level').value; };
$('#angle').oninput = () => { angleTouched = true; };
$('#angleBtns').innerHTML = [0, 45, 90, 135, 180, 225, 270, 315].map(a => `<button data-a="${a}">${a}&deg;</button>`).join('');
$('#angleBtns').onclick = e => { const a = e.target.dataset.a; if (a !== undefined) { $('#angle').value = a; angleTouched = true; } };

async function openCamera() {
  if (stream) { show('cam'); startAim(); return; }
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080}}});
  } catch (e) { toast(`Camera: ${e.message}. Allow camera access for this site and try again.`); return; }
  const v = $('#video'); v.srcObject = stream; await v.play().catch(() => {});
  show('cam'); $('#runBtn').disabled = false;
  $('#overlay').textContent = 'Frame the whole installation, then hold still.';
  try { wake = await navigator.wakeLock.request('screen'); } catch {}
  startAim();
}

// ---------------------------------------------------------------- live aim view
// While you aim, the bridge blinks the LEDs (all on / all off, about once a second) and the
// phone keeps, per pixel, the brightest and darkest value of the last two seconds. Their
// difference is the LED light at that pixel whatever the camera's lag, so the overlay shows
// green where the camera sees LEDs and red where they are clipped (too bright: bloom), and
// warns when LEDs reach the edge of the picture (some are probably out of frame). It doesn't
// count LEDs: at this size neighbouring LEDs merge, so a count would be badly low.
const AIM_W = 320;
const aim = {on: false, ring: [], blinkLevel: 255, session: null};
const aimCanvas = document.createElement('canvas');
const actx = aimCanvas.getContext('2d', {willReadFrequently: true});
function tickedIds() { return [...document.querySelectorAll('#ctrlList input:checked')].map(i => i.value); }
async function startAim() {
  if (aim.on || !stream) return;
  const ids = tickedIds();
  if (!ids.length) return;
  aim.on = true; aim.ring = [];
  try { aim.session = await api('/api/mapping/start', {ids, level: +$('#level').value}); }
  catch (e) { aim.on = false; $('#aimInfo').textContent = e.message; return; }
  aimBlink(); aimLook();
}
async function stopAim() {
  aim.on = false;
  while (aim.busy) await sleep(30);
  const cv = $('#aim'); cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
  $('#guideAim').innerHTML = '';
}
async function aimBlink() {
  let lit = false;
  while (aim.on) {
    aim.busy = true;
    lit = !lit;
    try { await api('/api/mapping/step', {session: aim.session.session, step: lit ? 'white' : 'black', level: aim.blinkLevel}); }
    catch { aim.busy = false; aim.on = false; return; }
    aim.busy = false;
    await sleep(650);
  }
}
async function aimLook() {
  const v = $('#video'), cv = $('#aim');
  while (aim.on) {
    await nextVideoFrame(v);
    const W = AIM_W, H = Math.max(1, Math.round(v.videoHeight * AIM_W / v.videoWidth));
    aimCanvas.width = W; aimCanvas.height = H;
    actx.drawImage(v, 0, 0, W, H);
    const d = actx.getImageData(0, 0, W, H).data, lum = new Uint8Array(W * H), top = new Uint8Array(W * H);
    for (let i = 0, p = 0; i < d.length; i += 4, p++) {
      lum[p] = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
      top[p] = Math.max(d[i], d[i + 1], d[i + 2]);
    }
    aim.ring.push({lum, top, t: performance.now()});
    while (aim.ring.length && performance.now() - aim.ring[0].t > 2000) aim.ring.shift();
    if (aim.ring.length < 6 || aim.ring.length % 3) continue;          // update the overlay ~every 3rd frame
    const N = W * H, hi = new Uint8Array(N), lo = new Uint8Array(N).fill(255), peak = new Uint8Array(N);
    for (const f of aim.ring) for (let p = 0; p < N; p++) {
      if (f.lum[p] > hi[p]) hi[p] = f.lum[p];
      if (f.lum[p] < lo[p]) lo[p] = f.lum[p];
      if (f.top[p] > peak[p]) peak[p] = f.top[p];
    }
    cv.width = W; cv.height = H;
    const ctx = cv.getContext('2d'), img = ctx.createImageData(W, H);
    let lit = 0, clipped = 0, edge = 0;
    const mx = Math.max(2, Math.round(W * 0.02)), my = Math.max(2, Math.round(H * 0.02));
    const amp = new Int16Array(N);
    for (let p = 0; p < N; p++) {
      amp[p] = hi[p] - lo[p];
      if (amp[p] < 25) continue;
      lit++;
      const clip = peak[p] >= 250;
      if (clip) clipped++;
      const x = p % W, y = (p / W) | 0;
      if (x < mx || y < my || x >= W - mx || y >= H - my) edge++;
      img.data[4 * p] = clip ? 255 : 0; img.data[4 * p + 1] = clip ? 40 : 255; img.data[4 * p + 2] = 60;
      img.data[4 * p + 3] = 170;
    }
    ctx.putImageData(img, 0, 0);
    const share = lit ? Math.round(100 * clipped / lit) : 0;
    const aimMsg = !lit ? '<strong class="bad">No LEDs seen yet: point the camera at the installation.</strong>'
      : `LEDs seen (green), ${share}% of them clipped (red).` +
        (edge > 3 ? ' <span class="bad">LEDs reach the edge of the picture: step back or zoom out so all of it is in frame.</span>' : ' All inside the frame.') +
        (share > 25 ? ' <span class="bad">Very bright: the capture will dim the LEDs to suit; you could also step back.</span>' : '');
    $('#aimInfo').innerHTML = aimMsg;
    $('#guideAim').innerHTML = aimMsg;
  }
}
$('#camBtn').onclick = async () => {
  await startSensors();                       // iOS asks for motion access here (needs this tap)
  const n = views ? views.views.length : 0;
  angleTouched = false;
  $('#angle').value = n ? (Math.round(views.views[n - 1].angle) + 45) % 360 : 0;
  await openCamera();
};

// ---------------------------------------------------------------- capture
const grabCanvas = document.createElement('canvas');
const gctx = grabCanvas.getContext('2d', {willReadFrequently: true});
function nextVideoFrame(v) {
  return new Promise(res => v.requestVideoFrameCallback ? v.requestVideoFrameCallback(() => res()) : setTimeout(res, 45));
}
// Average a few camera frames; returns luminance (and colour if asked) at analysis size.
async function grab(n, colour) {
  const v = $('#video'), W = Math.min(MAX_W, v.videoWidth), H = Math.round(v.videoHeight * W / v.videoWidth);
  grabCanvas.width = W; grabCanvas.height = H;
  const acc = new Float32Array(W * H * 3);
  for (let k = 0; k < n; k++) {
    await nextVideoFrame(v);
    gctx.drawImage(v, 0, 0, W, H);
    const d = gctx.getImageData(0, 0, W, H).data;
    for (let i = 0, j = 0; i < d.length; i += 4, j += 3) { acc[j] += d[i]; acc[j + 1] += d[i + 1]; acc[j + 2] += d[i + 2]; }
  }
  const lum = new Uint8ClampedArray(W * H), out = {W, H, lum};
  if (colour) { out.r = new Uint8ClampedArray(W * H); out.g = new Uint8ClampedArray(W * H); out.b = new Uint8ClampedArray(W * H); }
  for (let p = 0, j = 0; p < W * H; p++, j += 3) {
    const r = acc[j] / n, g = acc[j + 1] / n, b = acc[j + 2] / n;
    lum[p] = 0.299 * r + 0.587 * g + 0.114 * b;
    if (colour) { out.r[p] = r; out.g[p] = g; out.b[p] = b; }
  }
  return out;
}
function progress(i, n, label) {
  $('#progressWrap').hidden = false; $('#progress').style.width = `${Math.round(100 * i / n)}%`;
  $('#overlay').textContent = label;
}

// A small, fast grab (luminance, 240 px wide): for timing the camera's lag.
const tinyCanvas = document.createElement('canvas');
const tctx = tinyCanvas.getContext('2d', {willReadFrequently: true});
async function tinyLum() {
  const v = $('#video'), W = 240, H = Math.max(1, Math.round(v.videoHeight * 240 / v.videoWidth));
  tinyCanvas.width = W; tinyCanvas.height = H;
  await nextVideoFrame(v);
  tctx.drawImage(v, 0, 0, W, H);
  const d = tctx.getImageData(0, 0, W, H).data, out = new Float32Array(W * H);
  for (let i = 0, p = 0; i < d.length; i += 4, p++) out[p] = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
  return out;
}
// How much brighter than `dark`: the sum of per-pixel increases beyond noise. LEDs are small
// dots in a big picture, so the picture's average hardly moves; this counts only the dots.
function brighter(cur, dark) {
  let s = 0;
  for (let p = 0; p < cur.length; p++) { const d = cur[p] - dark[p] - 10; if (d > 0) s += d; }
  return s;
}
// How long from the bridge saying "frame is out" to the camera showing it: switch black -> all
// on and watch the LEDs appear. The phone's camera pipeline adds 100-400 ms, varying by phone
// and light; waiting less than that photographs the previous pattern.
// Wait until the camera picture stops changing, up to `max` ms; returns it. Only call it once
// the camera has had time to show the last switch: while it lags, the old picture is still too.
async function settled(max = 4000) {
  const t0 = performance.now();
  let prev = await tinyLum(), still = 0;
  while (performance.now() - t0 < max) {
    const cur = await tinyLum();
    let d = 0;
    for (let p = 0; p < cur.length; p++) { const x = Math.abs(cur[p] - prev[p]) - 10; if (x > 0) d += x; }
    still = d < 20 * cur.length / 21600 ? still + 1 : 0;
    prev = cur;
    if (still >= 4) break;
  }
  return prev;
}
async function cameraLag(sessionId) {
  // the LEDs went dark at start; the camera shows it some unknown time later, and until then
  // its picture is just as still, so first give it longer than any phone's lag
  await sleep(1200);
  const dark = await settled(1500);
  const t0 = performance.now();
  await api('/api/mapping/step', {session: sessionId, step: 'white'});
  const t1 = performance.now();
  const samples = [];
  while (performance.now() - t1 < 2500) samples.push([performance.now() - t1, brighter(await tinyLum(), dark)]);
  const lit = Math.max(...samples.map(s => s[1]));
  if (lit < 40 * dark.length / 21600)          // about two LEDs' worth at 240 x 90
    throw new Error('The camera sees no change when the LEDs come on: point it at the installation, darken the room, and check the controllers are ticked.');
  const reach = samples.find(s => s[1] >= 0.9 * lit);
  return {ms: Math.round(reach[0]), roundTrip: Math.round(t1 - t0)};
}
// Pick the LED level for the coding patterns from what the camera sees. Dimming LEDs is PWM
// (switching them on and off fast), which a phone's rolling shutter can catch as stripes;
// full brightness can clip and bloom. At each level, two photos of the same steady all-on
// frame: if they differ where the LEDs are, it flickers. Clipped = share of LED pixels at the
// sensor's maximum. Take the brightest level that doesn't flicker and hardly clips.
const LEVELS = [255, 160, 96, 56, 32];
async function chooseLevel(sessionId, settle) {
  await api('/api/mapping/step', {session: sessionId, step: 'black'});
  await sleep(settle);
  const dark = await grab(2, true);
  const tried = [];
  let mask = null;
  for (const L of LEVELS) {
    await api('/api/mapping/step', {session: sessionId, step: 'white', level: L});
    await sleep(settle);
    const a = await grab(1, true);
    await sleep(70);
    const b = await grab(1, true);
    if (!mask) {                                         // where the LEDs are, from full brightness
      mask = new Uint8Array(a.lum.length);
      for (let p = 0; p < mask.length; p++) mask[p] = a.lum[p] - dark.lum[p] > 25 ? 1 : 0;
    }
    let n = 0, sig = 0, diff = 0, clip = 0;
    for (let p = 0; p < mask.length; p++) {
      if (!mask[p]) continue;
      n++;
      sig += Math.max(0, a.lum[p] - dark.lum[p]);
      diff += Math.abs(a.lum[p] - b.lum[p]);
      if (Math.max(a.r[p], a.g[p], a.b[p]) >= 250) clip++;
    }
    const signal = n ? sig / n : 0;
    tried.push({level: L, signal: Math.round(signal), flicker: signal ? Math.round(100 * diff / n / signal) / 100 : 1,
                clipped: n ? Math.round(1000 * clip / n) / 10 : 0});
  }
  const steady = tried.filter(t => t.flicker < 0.08 && t.signal > 20);
  const pick = steady.find(t => t.clipped < 2)
    || steady.slice().sort((x, y) => x.clipped - y.clipped)[0]
    || tried[0];
  return {level: pick.level, tried};
}

// Ask the camera to stop adjusting itself (Android Chrome supports this; iOS Safari ignores it).
// Keeps the camera's current exposure / focus / colour temperature where the browser reports
// them, then checks the LEDs look the same as just before (`before`, an all-on photo): if
// locking changed the picture (some phones jump to a default exposure or focus), it unlocks
// again and says so. Returns what was locked, or ['undone: ...'].
async function lockCamera(before) {
  const track = stream && stream.getVideoTracks()[0];
  if (!track || !track.getCapabilities) return [];
  const caps = track.getCapabilities(), cur = track.getSettings ? track.getSettings() : {}, want = {}, locked = [];
  for (const [k, label, keep] of [['exposureMode', 'exposure', ['exposureTime', 'iso']],
                                  ['whiteBalanceMode', 'white balance', ['colorTemperature']],
                                  ['focusMode', 'focus', ['focusDistance']]]) {
    if (!(caps[k] && caps[k].includes('manual'))) continue;
    want[k] = 'manual'; locked.push(label);
    for (const s of keep) if (cur[s] !== undefined && caps[s]) want[s] = cur[s];
  }
  if (!locked.length) return [];
  try { await track.applyConstraints({advanced: [want]}); } catch { return []; }
  if (!before) return locked;
  await sleep(400);
  const after = await grab(2, false);
  let a = 0, b = 0;
  for (let p = 0; p < before.lum.length; p += 2) if (before.lum[p] > 60) { b += before.lum[p]; a += after.lum[p]; }
  const ratio = b ? a / b : 1;
  if (ratio < 0.7 || ratio > 1.4) {
    await unlockCamera();
    await sleep(400);
    return [`undone: the picture changed (x${ratio.toFixed(2)}) when locked`];
  }
  return locked;
}
async function unlockCamera() {
  const track = stream && stream.getVideoTracks()[0];
  if (!track || !track.getCapabilities) return;
  const caps = track.getCapabilities(), want = {};
  for (const k of ['exposureMode', 'whiteBalanceMode', 'focusMode'])
    if (caps[k] && caps[k].includes('continuous')) want[k] = 'continuous';
  try { await track.applyConstraints({advanced: [want]}); } catch {}
}

// One capture (one view): measure the camera (unless `reuse` gives the lag / wait / level from
// an earlier capture in the same spot and light), photograph every pattern, decode. Returns the
// decoded result (also kept in `decoded`) with the port report in `lastReport`; throws on failure.
let calib = null;
async function captureView(ids, angle, reuse) {
  cancelled = false; decoded = null;
  await stopAim();
  const minWait = Math.max(50, +$('#settle').value || 300);
  const atStart = {heading: sensor.heading, pitch: sensor.pitch, angle};
  const log = {retakes: {}, started: new Date().toISOString(), ua: navigator.userAgent};
  try {
    progress(0, 1, 'Taking over the controllers ...');
    session = await api('/api/mapping/start', {ids, level: +$('#level').value});
    sensor.maxRot = 0;
    let lag, settle, level;
    if (reuse) {
      ({lag, settle, level} = reuse);
      await api('/api/mapping/step', {session: session.session, step: 'black'});
      await sleep(Math.max(1200, settle));                       // the camera catches up with "off"
    } else {
      progress(0, 1, 'Timing the camera ...');
      lag = await cameraLag(session.session);
      settle = Math.max(minWait, Math.round(lag.ms * 1.3) + 60);
      progress(0, 1, 'Choosing the LED level ...');
      level = await chooseLevel(session.session, settle);
    }
    calib = {lag, settle, level};
    log.lag = lag; log.settle = settle; log.level = level; log.reused = !!reuse;
    aim.blinkLevel = level.level;
    // all-on first at that level: let exposure settle on the LEDs, then lock it (where allowed)
    await api('/api/mapping/step', {session: session.session, step: 'white', level: level.level});
    await sleep(settle);
    const shots = {};
    shots.white = await grab(4, true);
    log.locked = await lockCamera(shots.white);
    await sleep(150);
    const order = ['black', ...session.steps.filter(s => s !== 'black' && s !== 'white')];
    let prev = shots.white;
    for (let i = 0; i < order.length; i++) {
      if (cancelled) throw new Error('Stopped.');
      const step = order[i];
      progress(i + 1, order.length + 1, `Hold still ... ${i + 1} / ${order.length}`);
      await api('/api/mapping/step', {session: session.session, step});
      await sleep(settle);
      const colour = ['black', 'red', 'green', 'blue'].includes(step);
      let shot = await grab(step === 'black' ? 4 : 2, colour);
      // a photo that hardly differs from the last one was taken before the LEDs changed
      // (camera lag): wait and take it again
      if (shots.black && step !== 'black') {
        for (let tries = 0; tries < 4 && MapDecode.change(shot.lum, prev.lum, shots.white.lum, shots.black.lum) < 0.12; tries++) {
          log.retakes[step] = tries + 1;
          await sleep(settle / 2);
          shot = await grab(2, colour);
        }
        if (MapDecode.change(shot.lum, prev.lum, shots.white.lum, shots.black.lum) < 0.12)
          throw new Error(`The camera isn't keeping up (pattern ${step} never showed). Raise "Wait after each pattern" and try again.`);
      }
      shots[step] = prev = shot;
    }
    progress(order.length + 1, order.length + 1, 'Working it out ...');
    await sleep(30);
    const W = shots.black.W, H = shots.black.H, bits = [];
    for (let k = 0; k < session.bits; k++) bits.push([shots['b' + k].lum, shots['i' + k].lum]);
    decoded = MapDecode.decode({W, H, black: shots.black.lum, white: shots.white.lum, whiteEnd: shots.white_end.lum, bits,
                                colour: {black: shots.black, red: shots.red, green: shots.green, blue: shots.blue}},
                               session.total);
    decoded.photo = shots.white;
    decoded.shots = shots;
    decoded.log = log;
    decoded.meta = {...atStart, endHeading: sensor.heading, maxRot: Math.round(sensor.maxRot),
                    moved: decoded.stats.cameraMoved || sensor.maxRot > 25};
    await api('/api/mapping/step', {session: session.session, step: 'white'}).catch(() => {});
    lastReport = await api('/api/mapping/save', {session: session.session, points: decoded.points,
                                                 colours: decoded.colours, save: false}).catch(e => ({error: e.message}));
    if ($('#autoSend').checked) { sendPhotos.quiet = true; sendPhotos().finally(() => { sendPhotos.quiet = false; }); }
    return decoded;
  } finally {
    await unlockCamera();
    $('#progressWrap').hidden = true;
  }
}

$('#runBtn').onclick = async () => {
  const ids = tickedIds();
  if (!ids.length) { toast('Tick at least one controller'); return; }
  $('#runBtn').disabled = true;
  try {
    await captureView(ids, +$('#angle').value || 0, null);
    showResult();
  } catch (e) {
    toast(e.message); $('#overlay').textContent = e.message;
    $('#runBtn').disabled = false;
  }
};
$('#cancelBtn').onclick = async () => {
  cancelled = true;
  await stopAim();
  await api('/api/mapping/stop', {}).catch(() => {});
  stopCamera(); session = null; init();
};
function stopCamera() {
  if (stream) stream.getTracks().forEach(t => t.stop());
  stream = null; if (wake) wake.release().catch(() => {}); wake = null;
}

// ---------------------------------------------------------------- one capture's result
function portTable(rep, sofar) {
  return rep.map(c => `<h3>${esc(c.name)}: ${c.found} seen${sofar ? ' so far' : `, ${c.filled} to fill`}</h3>
    <table class="rep"><tr><th>Port</th><th>Set</th><th>Seen</th><th>${sofar ? 'Last seen' : 'Ends at'}</th><th>Colours</th><th></th></tr>
    ${c.ports.map(p => `<tr class="${p.ok ? '' : 'warn'}"><td>${p.port}</td><td>${p.configured}</td><td>${p.found}</td>
      <td>${p.last_seen || '-'}</td><td>${esc(p.colour_order || '?')}</td><td>${p.ok ? 'OK' : esc(p.notes.join('; '))}</td></tr>`).join('')}
    </table>`).join('');
}
function showResult() {
  show('result');
  const ph = decoded.photo, cv = $('#resultCanvas'), ctx = cv.getContext('2d');
  cv.width = ph.W; cv.height = ph.H;
  const img = ctx.createImageData(ph.W, ph.H);
  for (let p = 0; p < ph.W * ph.H; p++) {                // the all-on photo, dimmed, as the backdrop
    img.data[4 * p] = ph.r[p] * .35; img.data[4 * p + 1] = ph.g[p] * .35; img.data[4 * p + 2] = ph.b[p] * .35; img.data[4 * p + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  let g = 0;
  for (const c of session.controllers) {
    const ports = c.ports && c.ports.length ? c.ports : [[0, 0, c.n_led]];
    for (let i = 0; i < c.n_led; i++, g++) {
      const p = decoded.pixels[g];
      if (!p) continue;
      const pi = ports.findIndex(([, first, n]) => i >= first && i < first + n), port = ports[pi < 0 ? 0 : pi];
      ctx.fillStyle = `hsl(${Math.round(300 * (i - port[1]) / Math.max(1, port[2] - 1))}, 95%, 58%)`;   // red -> violet along the string
      ctx.beginPath(); ctx.arc(p[0], p[1], Math.max(1.2, ph.W / 500), 0, Math.PI * 2); ctx.fill();
    }
  }
  const s = decoded.stats;
  $('#resultTitle').textContent = `Found ${s.found} of ${s.total} LEDs at ${decoded.meta.angle}°`;
  $('#resultSummary').innerHTML = [
    decoded.meta.moved ? '<strong class="bad">The phone seems to have moved during the capture: try again with it held still.</strong>' : '',
    decoded.meta.pitch !== null && Math.abs(decoded.meta.pitch) > 15 ? `<strong class="bad">The phone was tilted ${Math.round(decoded.meta.pitch)}&deg;: for 3D, hold it upright.</strong>` : '',
    s.found < s.total * 0.5 ? '<strong class="bad">Less than half found: darken the room, get everything in frame, raise the brightness.</strong>' : '',
    'Each string runs red &rarr; yellow &rarr; green &rarr; blue &rarr; violet from its first LED: a right string is one smooth rainbow; a dot in the wrong colour is a misread LED.',
    `Camera lag ${decoded.log.lag.ms} ms, waited ${decoded.log.settle} ms per pattern` +
      (Object.keys(decoded.log.retakes).length ? `, ${Object.keys(decoded.log.retakes).length} photos retaken` : '') +
      (decoded.log.locked.length ? `, locked ${decoded.log.locked.join(', ')}` : '') +
      `, LED level ${decoded.log.level.level}` +
      ` (tried ${decoded.log.level.tried.map(t => `${t.level}: ${t.flicker >= 0.08 ? 'flickers' : t.clipped + '% clipped'}`).join(', ')})` + '.',
  ].filter(Boolean).join('<br>');
  const rep = lastReport && lastReport.controllers;
  $('#report').innerHTML = rep ? portTable(rep) : `<p class="bad">${esc((lastReport && lastReport.error) || 'no report')}</p>`;
  $('#keepBtn').disabled = !rep;
  $('#sendBtn').disabled = false; $('#sendBtn').textContent = 'Send photos to the bridge';
}
// Send this capture's photos to the bridge (instance/captures/), to look at what the camera saw.
function pngOf(shot) {
  const cv = document.createElement('canvas'); cv.width = shot.W; cv.height = shot.H;
  const ctx = cv.getContext('2d'), img = ctx.createImageData(shot.W, shot.H);
  for (let p = 0; p < shot.W * shot.H; p++) {
    img.data[4 * p] = shot.r ? shot.r[p] : shot.lum[p]; img.data[4 * p + 1] = shot.g ? shot.g[p] : shot.lum[p];
    img.data[4 * p + 2] = shot.b ? shot.b[p] : shot.lum[p]; img.data[4 * p + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return new Promise(res => cv.toBlob(res, 'image/png'));
}
$('#sendBtn').onclick = () => sendPhotos();
async function sendPhotos() {
  const btn = $('#sendBtn'); btn.disabled = true;
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + `_${Math.round(decoded.meta.angle)}deg`;
  try {
    const names = Object.keys(decoded.shots);
    for (let i = 0; i < names.length; i++) {
      btn.textContent = `Sending ${i + 1} / ${names.length + 1} ...`;
      const r = await fetch(`/api/mapping/captures/${stamp}/${names[i]}.png`,
                            {method: 'POST', headers: {'Content-Type': 'image/png'}, body: await pngOf(decoded.shots[names[i]])});
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    }
    const info = {session: session, stats: decoded.stats, log: decoded.log, meta: decoded.meta,
                  size: [decoded.photo.W, decoded.photo.H], points: decoded.points, colours: decoded.colours,
                  report: lastReport, screen: [screen.width, screen.height, devicePixelRatio]};
    const r = await fetch(`/api/mapping/captures/${stamp}/capture.json`,
                          {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(info)});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    btn.textContent = 'Sent to the bridge';
    if (!sendPhotos.quiet) toast(`Saved on the bridge as ${stamp}: download it from the desk page, Camera mapping panel.`, 7000);
  } catch (e) { btn.textContent = 'Send photos to the bridge'; btn.disabled = false; toast(`Sending failed: ${e.message}`); }
}
$('#retryBtn').onclick = () => { show('cam'); $('#runBtn').disabled = false; $('#progressWrap').hidden = true; startAim();
                                 $('#overlay').textContent = 'Frame the whole installation, then hold still.'; };
async function keepView() {
  const r = await api('/api/mapping/view', {session: session.session, angle: decoded.meta.angle, points: decoded.points,
    colours: decoded.colours, heading: decoded.meta.heading, pitch: decoded.meta.pitch, moved: decoded.meta.moved});
  await loadViews();
  return r;
}
$('#keepBtn').onclick = async () => {
  $('#keepBtn').disabled = true;
  try {
    const r = await keepView();
    show('kept');
    const n = r.views.views.length;
    $('#keptTitle').textContent = `View ${n} kept (${decoded.meta.angle}°)`;
    $('#keptText').innerHTML = `${r.new} LEDs new in this view; ${r.views.seen_any} of ${r.views.total} seen so far.` +
      (r.replaced ? '<br><strong class="bad">Earlier views were for other controllers and were discarded.</strong>' : '') +
      `<br><br>The installation now shows <span class="g">green</span>: found in this view, <span class="b">blue</span>: found
       before, <span class="r">red</span>: not found yet. For a 3D piece, walk about 45&deg; further round to your right
       (to where you see red) and capture again; then <em>Build map</em>.`;
    $('#keptReport').innerHTML = portTable(r.controllers, true);
  } catch (e) { toast(e.message); $('#keepBtn').disabled = false; }
};
// ---------------------------------------------------------------- guided walk-around
// One continuous session: capture, keep, then direct the user round the object with the
// compass ("walk right ... 28 of 45 degrees") and the tilt sensor ("hold it upright"), and
// capture by itself as soon as the phone is at the next angle, upright and steady. Each
// capture needs the phone still (an LED's number is read from the same pixels across ~25
// photos), so it walks, stops, holds, walks. Without a compass it asks for a tap instead.
const GUIDE = {step: 45, views: 8, tol: 9, upright: 12, steadyRot: 10, steadyMs: 1200};
let guide = null;
function gauge(done, target) {
  const pct = Math.max(0, Math.min(100, 100 * done / target));
  return `<div class="gauge"><div style="width:${pct}%"></div></div>`;
}
function tiltText() {
  if (sensor.pitch === null) return '';
  const t = Math.round(sensor.pitch);
  return Math.abs(t) <= GUIDE.upright ? 'upright' : t > 0 ? 'tilt the top of the phone towards you' : 'tilt the top of the phone away from you';
}
function say(big, small) { $('#guideBig').innerHTML = big; $('#guideSmall').innerHTML = small || ''; }
async function runGuided() {
  const ids = tickedIds();
  if (!ids.length) { toast('Tick at least one controller'); return; }
  guide = {stop: false, finish: false, kept: 0, wait: null};
  $('#guidePanel').hidden = false; $('#camButtons').hidden = true; $('#guideButtons').hidden = false;
  await stopAim();
  try {
    if (views && views.views.length && !confirm('Add to the views already kept? (Cancel discards them and starts afresh.)')) {
      await api('/api/mapping/views', undefined, 'DELETE'); await loadViews();
    }
    let target = views && views.views.length ? (Math.round(views.views[views.views.length - 1].angle) + GUIDE.step) % 360 : 0;
    let first = !(views && views.views.length);
    let walkCalib = null;                               // measured on this walk's first capture
    while (!guide.stop && !guide.finish) {
      if (!first) {
        const ok = await walkTo(target);
        if (!ok) break;
      }
      say('Hold still', 'capturing this view: about 10-20 s');
      let d;
      // the view's angle is where the compass says the phone is, not the target it was walking to
      const here = first ? 0 : (compassAngle() ?? target);
      try { d = await captureView(ids, here, walkCalib); walkCalib = calib; }
      catch (e) { if (guide.stop) break; say('That capture failed', esc(e.message) + '<br>Trying again in a moment.'); await sleep(2500); continue; }
      if (d.meta.moved) { say('You moved', 'hold the phone still while it captures: trying again'); await sleep(2000); continue; }
      if (d.stats.found < d.stats.total * 0.1) {
        say(`Hardly any LEDs found (${d.stats.found} of ${d.stats.total})`,
            'Check the green on the picture shows the LEDs, the whole piece is in frame and the room is dark; ' +
            `then hold still. (LED level ${d.log.level.level}, camera lag ${d.log.lag.ms} ms` +
            (d.log.locked.length ? `, camera lock: ${esc(d.log.locked.join(', '))}` : '') + '.) Trying again in a moment.');
        walkCalib = null;                               // measure afresh next time
        await aimFor(5000);
        continue;
      }
      const r = await keepView();
      guide.kept = r.views.views.length;
      say(`View ${guide.kept} kept: ${r.new} new LEDs`, `${r.views.seen_any} of ${r.views.total} seen so far. On the LEDs: green = this view, blue = before, red = not yet.`);
      await sleep(1800);
      first = false;
      if (guide.kept >= GUIDE.views || (target + GUIDE.step) % 360 === 0) break;
      target = (target + GUIDE.step) % 360;
    }
    if (!guide.stop && views && views.views.length) { $('#guidePanel').hidden = true; await build(); }
  } finally {
    guide = null;
    $('#guidePanel').hidden = true; $('#camButtons').hidden = false; $('#guideButtons').hidden = true;
    $('#runBtn').disabled = false;
  }
}
// Direct the user to `target` degrees round the object; resolve true when there, upright and
// steady (or on a tap without a compass), false if stopped / finished.
// Show the live detection overlay for a while (LEDs blinking), e.g. to re-frame after a failure.
async function aimFor(ms) {
  await startAim();
  await sleep(ms);
}
async function walkTo(target) {
  let steadySince = null;
  startAim();                                           // live detection overlay while walking
  $('#overlay').textContent = '';
  return new Promise(done => {
    let over = false;
    const resolve = v => { if (over) return; over = true; if (guide) guide.wait = null; $('#hereBtn').hidden = true; done(v); };
    guide.wait = resolve;
    const tick = () => {
      if (over) return;
      if (!guide || guide.stop || guide.finish) { resolve(false); return; }
      const a = compassAngle();
      if (a === null) {
        say(`Walk about ${GUIDE.step}&deg; to your right`, 'keep pointing at the middle, same distance and height, then tap <em>I\'m there</em> (no compass on this phone)');
        $('#hereBtn').hidden = false;
        setTimeout(tick, 300);
        return;
      }
      $('#hereBtn').hidden = true;
      const prevTarget = (target - GUIDE.step + 360) % 360;
      let moved = (a - prevTarget + 360) % 360;
      if (moved > 300) moved -= 360;                               // a little the wrong way
      const off = ((a - target + 540) % 360) - 180;               // + = past the target
      const tilt = tiltText(), upright = tilt === 'upright' || tilt === '';
      const steady = sensor.rot < GUIDE.steadyRot;
      if (Math.abs(off) <= GUIDE.tol && upright && steady) {
        steadySince = steadySince || performance.now();
        const left = GUIDE.steadyMs - (performance.now() - steadySince);
        say('Stop here and hold still', left > 0 ? `capturing in ${Math.ceil(left / 400)} ...` : 'capturing');
        if (left <= 0) { resolve(true); return; }
      } else {
        steadySince = null;
        if (Math.abs(off) <= GUIDE.tol) say(steady ? 'Hold the phone upright' : 'Hold still', tilt === 'upright' ? '' : tilt);
        else if (off < 0) say(`Walk to your right: ${Math.max(0, Math.round(moved))}&deg; of ${GUIDE.step}&deg;`,
                              gauge(moved, GUIDE.step) + `keep pointing at the middle; ${tilt || 'same height'}`);
        else say(`Too far: step back to your left ${Math.round(off)}&deg;`, gauge(GUIDE.step + off, GUIDE.step) + tilt);
      }
      setTimeout(tick, 150);
    };
    tick();
  });
}
$('#guideBtn').onclick = () => runGuided();
$('#hereBtn').onclick = () => { if (guide && guide.wait) guide.wait(true); };
$('#finishBtn').onclick = () => { if (guide) { guide.finish = true; cancelled = true; if (guide.wait) guide.wait(false); } };
$('#stopGuideBtn').onclick = async () => {
  if (guide) { guide.stop = true; cancelled = true; if (guide.wait) guide.wait(false); }
  await api('/api/mapping/stop', {}).catch(() => {});
};

$('#moreBtn').onclick = () => {
  const n = views.views.length;
  angleTouched = false;
  $('#angle').value = compassAngle() ?? (Math.round(views.views[n - 1].angle) + 45) % 360;
  $('#runBtn').disabled = false; $('#progressWrap').hidden = true; openCamera();
};

// ---------------------------------------------------------------- build (combine) and save
async function build() {
  try {
    built = await api('/api/mapping/combine', {mode: 'auto', mirror, save: false});
  } catch (e) { toast(e.message); return; }
  show('built');
  const st = built.stats, three = built.mode === 'around';
  $('#builtTitle').textContent = three ? `3D map from ${built.views.length} views` : `Map from ${built.views.length} view${built.views.length > 1 ? 's' : ''}`;
  drawPreview($('#pvFront'), built.points, 0, 1);
  drawPreview($('#pvTop'), built.points, 0, 2);
  $('#pvTop').parentElement.hidden = !three;
  $('#builtText').innerHTML = [
    three ? `${st.multi_view} LEDs placed from two or more angles, ${st.single_view} from one angle (depth estimated from
             their neighbours), ${st.unseen} not seen (filled in along their string).`
          : `${st.multi_view} LEDs seen in more than one view, ${st.unseen} not seen (filled in along their string).`,
    three ? `Angles used: ${built.views.map(v => `${v.angle}&deg;${Math.abs(v.refined_angle - v.angle) >= 1 ? ` (&rarr; ${v.refined_angle}&deg;)` : ''}`).join(', ')}.` : '',
    three ? 'If the top view looks mirrored compared with the real object (e.g. you walked left instead of right), press <em>Flip</em>.' : '',
  ].filter(Boolean).join('<br>');
  $('#flipBtn').hidden = !three;
}
function drawPreview(cv, pts, a, b) {
  const W = 300, H = 300, ctx = cv.getContext('2d');
  cv.width = W; cv.height = H;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  const have = pts.filter(Boolean);
  if (!have.length) return;
  const lo = [0, 1, 2].map(k => Math.min(...have.map(p => p[k]))), hi = [0, 1, 2].map(k => Math.max(...have.map(p => p[k])));
  const s = 270 / Math.max(hi[a] - lo[a], hi[b] - lo[b], 1e-9);
  pts.forEach((p, i) => {
    if (!p) return;
    ctx.fillStyle = `hsl(${(i / pts.length) * 300}, 90%, 60%)`;      // colour along the chain
    ctx.fillRect(15 + (p[a] - lo[a]) * s, 15 + (p[b] - lo[b]) * s, 2, 2);
  });
}
$('#buildBtn').onclick = () => build();
$('#flipBtn').onclick = () => { mirror = !mirror; build(); };
$('#backBtn').onclick = () => init();
$('#saveBtn').onclick = async () => {
  $('#saveBtn').disabled = true;
  try {
    const r = await api('/api/mapping/combine', {mode: 'auto', mirror, save: true});
    toast(`Saved: ${r.controllers.map(c => `${c.name} ${c.found} seen + ${c.filled} filled`).join(', ')}.`, 6000);
    stopCamera(); session = null;
    showCheck(r.mode === 'around');
  } catch (e) { toast(e.message); }
  $('#saveBtn').disabled = false;
};

// ---------------------------------------------------------------- check the saved map
const SWEEPS = [['sweep_x', 'Left to right'], ['sweep_y', 'Top to bottom'], ['sweep_z', 'Front to back'],
                ['spin', 'Spin around'], ['ring', 'Ring from centre'], ['map_rainbow', 'Rainbow across']];
function showCheck(three) {
  show('check');
  $('#sweeps').innerHTML = SWEEPS.filter(([k]) => three || (k !== 'sweep_z' && k !== 'spin'))
    .map(([k, label]) => `<button class="big" data-p="${k}">${label}</button>`).join('');
}
$('#sweeps').onclick = async e => {
  const p = e.target.dataset.p;
  if (!p) return;
  const ids = built ? built.controllers.map(c => c.id) : undefined;
  try { await api('/api/test', {ids, mode: 'pattern', pattern: p, period: +$('#period').value || 3, seconds: 120});
        toast(`Running ${e.target.textContent}`); }
  catch (err) { toast(err.message); }
};
$('#stopBtn').onclick = () => api('/api/test', {mode: 'stop'}).then(() => toast('Stopped')).catch(e => toast(e.message));
$('#againBtn').onclick = () => { api('/api/test', {mode: 'stop'}).catch(() => {}); init(); };

document.addEventListener('visibilitychange', () => {
  if (document.hidden && session && !decoded) { cancelled = true; api('/api/mapping/stop', {}).catch(() => {}); }
});
init().catch(e => toast(e.message));
