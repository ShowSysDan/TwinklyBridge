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
  if (stream) { show('cam'); return; }
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080}}});
  } catch (e) { toast(`Camera: ${e.message}. Allow camera access for this site and try again.`); return; }
  const v = $('#video'); v.srcObject = stream; await v.play().catch(() => {});
  show('cam'); $('#runBtn').disabled = false;
  $('#overlay').textContent = 'Frame the whole installation, then hold still.';
  try { wake = await navigator.wakeLock.request('screen'); } catch {}
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

$('#runBtn').onclick = async () => {
  const ids = [...document.querySelectorAll('#ctrlList input:checked')].map(i => i.value);
  if (!ids.length) { toast('Tick at least one controller'); return; }
  $('#runBtn').disabled = true; cancelled = false; decoded = null;
  const settle = Math.max(50, +$('#settle').value || 300);
  const atStart = {heading: sensor.heading, pitch: sensor.pitch, angle: +$('#angle').value || 0};
  try {
    progress(0, 1, 'Taking over the controllers ...');
    session = await api('/api/mapping/start', {ids, level: +$('#level').value});
    sensor.maxRot = 0;
    const shots = {}, steps = session.steps;
    for (let i = 0; i < steps.length; i++) {
      if (cancelled) return;
      const step = steps[i];
      progress(i, steps.length, `Hold still ... ${i + 1} / ${steps.length}`);
      await api('/api/mapping/step', {session: session.session, step});
      await sleep(settle);
      shots[step] = await grab(step === 'black' || step === 'white' ? 4 : 2,
                               ['black', 'white', 'red', 'green', 'blue'].includes(step));
    }
    progress(steps.length, steps.length, 'Working it out ...');
    await sleep(30);
    const W = shots.black.W, H = shots.black.H, bits = [];
    for (let k = 0; k < session.bits; k++) bits.push([shots['b' + k].lum, shots['i' + k].lum]);
    decoded = MapDecode.decode({W, H, black: shots.black.lum, white: shots.white.lum, whiteEnd: shots.white_end.lum, bits,
                                colour: {black: shots.black, red: shots.red, green: shots.green, blue: shots.blue}},
                               session.total);
    decoded.photo = shots.white;
    decoded.meta = {...atStart, moved: decoded.stats.cameraMoved || sensor.maxRot > 25};
    await api('/api/mapping/step', {session: session.session, step: 'white'}).catch(() => {});
    lastReport = await api('/api/mapping/save', {session: session.session, points: decoded.points,
                                                 colours: decoded.colours, save: false}).catch(e => ({error: e.message}));
    showResult();
  } catch (e) {
    toast(e.message); $('#overlay').textContent = e.message;
    $('#runBtn').disabled = false; $('#progressWrap').hidden = true;
  }
};
$('#cancelBtn').onclick = async () => {
  cancelled = true;
  await api('/api/mapping/stop', {}).catch(() => {});
  stopCamera(); session = null; init();
};
function stopCamera() {
  if (stream) stream.getTracks().forEach(t => t.stop());
  stream = null; if (wake) wake.release().catch(() => {}); wake = null;
}

// ---------------------------------------------------------------- one capture's result
const PORT_COLOURS = ['#ff4d4d', '#4dff6a', '#4da3ff', '#ffb84d', '#ff4dff', '#4dffff', '#ffff4d', '#a64dff'];
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
      const pi = ports.findIndex(([, first, n]) => i >= first && i < first + n);
      ctx.fillStyle = PORT_COLOURS[(pi < 0 ? 0 : pi) % PORT_COLOURS.length];
      ctx.beginPath(); ctx.arc(p[0], p[1], Math.max(1.2, ph.W / 500), 0, Math.PI * 2); ctx.fill();
    }
  }
  const s = decoded.stats;
  $('#resultTitle').textContent = `Found ${s.found} of ${s.total} LEDs at ${decoded.meta.angle}°`;
  $('#resultSummary').innerHTML = [
    decoded.meta.moved ? '<strong class="bad">The phone seems to have moved during the capture: try again with it held still.</strong>' : '',
    decoded.meta.pitch !== null && Math.abs(decoded.meta.pitch) > 15 ? `<strong class="bad">The phone was tilted ${Math.round(decoded.meta.pitch)}&deg;: for 3D, hold it upright.</strong>` : '',
    s.found < s.total * 0.5 ? '<strong class="bad">Less than half found: darken the room, get everything in frame, raise the brightness.</strong>' : '',
    'Dots are coloured by port.',
  ].filter(Boolean).join('<br>');
  const rep = lastReport && lastReport.controllers;
  $('#report').innerHTML = rep ? portTable(rep) : `<p class="bad">${esc((lastReport && lastReport.error) || 'no report')}</p>`;
  $('#keepBtn').disabled = !rep;
}
$('#retryBtn').onclick = () => { show('cam'); $('#runBtn').disabled = false; $('#progressWrap').hidden = true;
                                 $('#overlay').textContent = 'Frame the whole installation, then hold still.'; };
$('#keepBtn').onclick = async () => {
  $('#keepBtn').disabled = true;
  try {
    const r = await api('/api/mapping/view', {session: session.session, angle: decoded.meta.angle, points: decoded.points,
      colours: decoded.colours, heading: decoded.meta.heading, pitch: decoded.meta.pitch, moved: decoded.meta.moved});
    await loadViews();
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
