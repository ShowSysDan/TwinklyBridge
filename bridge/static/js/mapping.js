// Phone page for camera mapping: opens the rear camera, steps the bridge through its
// Gray-coded frames, photographs each, decodes (mapdecode.js), shows the result and saves it.
'use strict';
const $ = s => document.querySelector(s);
const HTTPS_PORT = +$('#mappingScript').dataset.httpsPort || 0;
const MAX_W = 960;                    // analysis width: plenty for LEDs a few pixels apart
let stream = null, session = null, decoded = null, wake = null, cancelled = false, lastReport = null;

function toast(msg, ms) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), ms || (msg.length > 80 ? 9000 : 3000));
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'},
                                                         body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `${path}: HTTP ${r.status}`);
  return j;
}
function esc(s) { return String(s).replace(/[&<>"]/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[ch])); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
function show(id) { for (const s of ['insecure', 'setup', 'cam', 'result']) $('#' + s).hidden = s !== id; }

// ---------------------------------------------------------------- setup
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
  $('#ctrlList').innerHTML = st.controllers.length ? st.controllers.map(c =>
    `<label><input type="checkbox" value="${esc(c.id)}" ${c.enabled ? 'checked' : ''}>
       <span><strong>${esc(c.name)}</strong> ${esc(c.ip)}<br><span class="hint">${c.n_led} LEDs, ${c.status}${c.has_map ? `, map: ${esc(c.map_source)}` : ', no map'}</span></span></label>`).join('')
    : '<p class="hint">No controllers on the bridge yet. Add them on the main page first.</p>';
  show('setup');
}
$('#level').oninput = () => { $('#levelV').textContent = $('#level').value; };

$('#camBtn').onclick = async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080}}});
  } catch (e) { toast(`Camera: ${e.message}. Allow camera access for this site and try again.`); return; }
  const v = $('#video'); v.srcObject = stream; await v.play().catch(() => {});
  show('cam'); $('#runBtn').disabled = false;
  try { wake = await navigator.wakeLock.request('screen'); } catch {}
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
  try {
    progress(0, 1, 'Taking over the controllers ...');
    session = await api('/api/mapping/start', {ids, level: +$('#level').value});
    const shots = {}, steps = session.steps;
    for (let i = 0; i < steps.length; i++) {
      if (cancelled) return;
      const step = steps[i];
      progress(i, steps.length, `Hold still ... ${i + 1} / ${steps.length}`);
      await api('/api/mapping/step', {session: session.session, step});
      await sleep(settle);
      shots[step] = await grab(step === 'black' || step === 'white' ? 4 : 2,
                               ['black', 'red', 'green', 'blue'].includes(step));
    }
    progress(steps.length, steps.length, 'Working it out ...');
    await sleep(30);
    const W = shots.black.W, H = shots.black.H, bits = [];
    for (let k = 0; k < session.bits; k++) bits.push([shots['b' + k].lum, shots['i' + k].lum]);
    decoded = MapDecode.decode({W, H, black: shots.black.lum, white: shots.white.lum, whiteEnd: shots.white_end.lum, bits,
                                colour: {black: shots.black, red: shots.red, green: shots.green, blue: shots.blue}},
                               session.total);
    decoded.photo = shots.white;
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

// ---------------------------------------------------------------- result
const PORT_COLOURS = ['#ff4d4d', '#4dff6a', '#4da3ff', '#ffb84d', '#ff4dff', '#4dffff', '#ffff4d', '#a64dff'];
function showResult() {
  show('result');
  const ph = decoded.photo, cv = $('#resultCanvas'), ctx = cv.getContext('2d');
  cv.width = ph.W; cv.height = ph.H;
  const img = ctx.createImageData(ph.W, ph.H);
  for (let p = 0; p < ph.W * ph.H; p++) {                // the all-on photo, dimmed, as the backdrop
    img.data[4 * p] = ph.r ? ph.r[p] * .35 : ph.lum[p] * .35; img.data[4 * p + 1] = ph.lum[p] * .35;
    img.data[4 * p + 2] = ph.lum[p] * .35; img.data[4 * p + 3] = 255;
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
  $('#resultTitle').textContent = `Found ${s.found} of ${s.total} LEDs`;
  $('#resultSummary').innerHTML = [
    s.cameraMoved ? '<strong class="bad">The camera seems to have moved during the run: try again with the phone held still.</strong>' : '',
    s.found < s.total * 0.5 ? '<strong class="bad">Less than half found: darken the room, get everything in frame, raise the brightness.</strong>' : '',
    `Dots are coloured by port. LEDs not seen are filled in along their string when you save.`,
  ].filter(Boolean).join('<br>');
  const rep = lastReport && lastReport.controllers;
  $('#report').innerHTML = !rep ? `<p class="bad">${esc((lastReport && lastReport.error) || 'no report')}</p>`
    : rep.map(c => `<h3>${esc(c.name)}: ${c.found} seen, ${c.filled} to fill</h3>
      <table class="rep"><tr><th>Port</th><th>Set</th><th>Seen</th><th>Ends at</th><th>Colours</th><th></th></tr>
      ${c.ports.map(p => `<tr class="${p.ok ? '' : 'warn'}"><td>${p.port}</td><td>${p.configured}</td><td>${p.found}</td>
        <td>${p.last_seen || '-'}</td><td>${esc(p.colour_order || '?')}</td><td>${p.ok ? 'OK' : esc(p.notes.join('; '))}</td></tr>`).join('')}
      </table>`).join('');
  $('#saveBtn').disabled = !!(lastReport && lastReport.error);
}
$('#saveBtn').onclick = async () => {
  $('#saveBtn').disabled = true;
  try {
    const r = await api('/api/mapping/save', {session: session.session, points: decoded.points, colours: decoded.colours});
    toast(`Saved: ${r.controllers.map(c => `${c.name} ${c.found} seen + ${c.filled} filled`).join(', ')}. ` +
          `${r.totals.parameters} parameters mapped.`, 8000);
    stopCamera(); session = null; setTimeout(init, 1500);
  } catch (e) { toast(e.message); $('#saveBtn').disabled = false; }
};
$('#retryBtn').onclick = () => { show('cam'); $('#runBtn').disabled = false; $('#progressWrap').hidden = true;
                                 $('#overlay').textContent = 'Frame the whole installation, then hold still.'; };
document.addEventListener('visibilitychange', () => {
  if (document.hidden && session && !decoded) { cancelled = true; api('/api/mapping/stop', {}).catch(() => {}); }
});
init().catch(e => toast(e.message));
