const $ = s => document.querySelector(s);
let status = null, maps = [], frames = {}, hit = [];      // hit: [{cid,index,x,y}] for click lookup
let toastT = null;

function toast(msg, ms) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), ms || (msg.length > 80 ? 9000 : 2600));
}
async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `${opts.method || 'GET'} ${path}: HTTP ${r.status}`);
  return j;
}

// ---------------------------------------------------------------- status + controllers
function portOf(c, i) {
  for (const [pid, first, n] of c.ports || []) if (i >= first && i < first + n) return [pid, i - first + 1];
  return [-1, i + 1];
}
function renderControllers() {
  const host = $('#ctrls'); host.innerHTML = '';
  if (!status.controllers.length) { host.innerHTML = '<p class="hint">No controllers yet. Add one by IP below.</p>'; return; }
  for (const c of status.controllers) {
    const el = document.createElement('div'); el.className = 'ctrl' + (c.enabled ? '' : ' off');
    const lamp = c.status === 'streaming' ? 'on' : c.status === 'online' ? 'ok' : c.status === 'error' ? 'bad' : '';
    const state = c.testing ? `test: ${c.test_pattern === 'frame' ? 'pixel / flash' : c.test_pattern}` : c.status === 'streaming' ? 'streaming' : c.status;
    const uni = c.source === 'ddp'
      ? (c.ddp_range.length ? `DDP bytes ${c.ddp_range[0]} to ${c.ddp_range[1]}` : 'DDP')
      : (c.universes.length ? (c.universes.length === 1 ? `universe ${c.universes[0]}` : `universes ${c.universes[0]} to ${c.universes[c.universes.length-1]}`) : '');
    el.innerHTML = `
      <div class="top"><span class="name">${esc(c.name)}</span><span class="ip">${c.ip}</span>
        <span class="state"><span class="lamp ${lamp}"></span>${state}</span></div>
      <div class="facts">${c.n_led ? `${c.n_led} LEDs, ${c.profile}` : 'not read yet'}${c.product ? `, ${esc(c.product)}` : ''}${c.fw ? ` fw ${esc(c.fw)}` : ''}${c.api ? ` (api ${c.api})` : ''}
        ${c.group && c.group.mode && c.group.mode !== 'none' ? `, group ${esc(c.group.mode)} offset ${c.group.offset}` : ''}
        <br>${uni}${c.has_map ? `, map ${c.map_points} points (${esc(c.map_source)})` : ', no map'}
        ${c.parameters ? `<br><span class="param">${(c.parameters / c.bpp).toLocaleString()} pixels × ${c.bpp} (${c.bpp === 4 ? 'RGBW' : 'RGB'}) = <strong>${c.parameters.toLocaleString()}</strong> parameters</span>` : ''}</div>
      <div class="patch">
        <select data-k="source" title="Input protocol"><option value="sacn" ${c.source==='sacn'?'selected':''}>sACN</option><option value="ddp" ${c.source==='ddp'?'selected':''}>DDP</option></select>
        ${c.source === 'ddp'
          ? `<label>Start byte</label><input type="number" min="1" value="${c.ddp_start}" data-k="ddp_start">`
          : `<label>Universe</label><input type="number" min="1" max="63999" value="${c.universe}" data-k="universe">
             <label>Channel</label><input type="number" min="1" max="512" value="${c.channel}" data-k="channel">`}
        ${c.profile === 'RGBW' ? `<label>W arrives as</label><select data-k="input_order">${['RGBW','WRGB','GRBW'].map(o => `<option ${o===c.input_order?'selected':''}>${o}</option>`).join('')}</select>` : ''}
        <label><input type="checkbox" data-k="enabled" ${c.enabled ? 'checked' : ''}> enabled</label>
      </div>
      <div class="actions">
        <button data-a="pull">Pull map</button>
        <button data-a="ports" title="One row per port, LEDs in chain order: exact addressing, not physical positions">Map from ports</button>
        <button data-a="probe" title="Ask the controller where its map is (read-only) and show the answers">Diagnose</button>
        <button data-a="csv">Upload CSV</button>
        ${c.has_map ? `<button data-a="dl">Download CSV</button>` : ''}
        <button data-a="solid">Flash red</button>
        <button data-a="off">Blackout 3 s</button>
        <button data-a="connect">Reconnect</button>
        <button data-a="remove" class="quiet">Remove</button>
        <input type="file" accept=".csv,text/csv" hidden data-a="csvfile">
      </div>
      ${c.error ? `<div class="err">${esc(c.error)}</div>` : ''}
      ${c.map_error ? `<div class="err">${esc(c.map_error)}</div>` : ''}`;
    el.querySelectorAll('[data-k]').forEach(inp => inp.addEventListener('change', async () => {
      const k = inp.dataset.k, v = inp.type === 'checkbox' ? inp.checked : inp.value;
      try { await api(`/api/controllers/${c.id}`, {method: 'PUT', body: JSON.stringify({[k]: v})}); toast('Patched'); refresh(); }
      catch (e) { toast(e.message); }
    }));
    el.querySelector('[data-a=pull]').onclick = async b => {
      b.target.disabled = true;
      try { const r = await api(`/api/controllers/${c.id}/map/pull`, {method: 'POST'});
            toast(`Pulled ${r.points} points (${r.source}): ${r.parameters} parameters, ${r.totals.parameters} across all maps`); await loadMaps(); }
      catch (e) { toast(e.message); } refresh();
    };
    el.querySelector('[data-a=ports]').onclick = async b => {
      if (c.has_map && !confirm(`Replace ${c.name}'s map (${c.map_source}) with one built from its ports?`)) return;
      try { const r = await api(`/api/controllers/${c.id}/map/ports`, {method: 'POST'});
            toast(`Map from ports: ${r.points} points, ${r.parameters} parameters`); await loadMaps(); }
      catch (e) { toast(e.message); } refresh();
    };
    el.querySelector('[data-a=probe]').onclick = async b => {
      b.target.disabled = true; toast('Asking the controller ...');
      try { showProbe(await api(`/api/controllers/${c.id}/probe`, {method: 'POST'})); }
      catch (e) { toast(e.message); }
      b.target.disabled = false;
    };
    const file = el.querySelector('[data-a=csvfile]');
    el.querySelector('[data-a=csv]').onclick = () => file.click();
    file.onchange = async () => {
      const fd = new FormData(); fd.append('file', file.files[0]);
      try { const r = await (await fetch(`/api/controllers/${c.id}/map/csv`, {method: 'POST', body: fd})).json();
            if (r.error) throw new Error(r.error); toast(`Loaded ${r.points} points: ${r.parameters} parameters`); await loadMaps(); }
      catch (e) { toast(e.message); } refresh();
    };
    const dl = el.querySelector('[data-a=dl]'); if (dl) dl.onclick = () => { location.href = `/api/controllers/${c.id}/map.csv`; };
    el.querySelector('[data-a=solid]').onclick = () => test(c.id, {mode: 'solid', rgb: [255, 0, 0], seconds: 3});
    el.querySelector('[data-a=off]').onclick = () => test(c.id, {mode: 'off', seconds: 3});
    el.querySelector('[data-a=connect]').onclick = async () => { await api(`/api/controllers/${c.id}/connect`, {method: 'POST'}); refresh(); };
    el.querySelector('[data-a=remove]').onclick = async () => {
      if (!confirm(`Remove ${c.name} (${c.ip}) from the bridge?`)) return;
      await api(`/api/controllers/${c.id}`, {method: 'DELETE'}); await loadMaps(); refresh();
    };
    host.appendChild(el);
  }
}
function showProbe(r) {
  const lines = [`${r.controller} ${r.ip} ${r.product} fw ${r.fw} api ${r.api}, ${r.n_led} LEDs`,
                 `group ${JSON.stringify(r.group)}  ports ${JSON.stringify(r.ports)}`, ''];
  for (const x of r.results)
    lines.push(`${String(x.http ?? 'ERR').padEnd(4)} ${x.path.padEnd(36)} ${x.code !== undefined ? 'code ' + x.code : ''}` +
               `${x.coordinates ? '  COORDINATES ' + x.coordinates : ''}${x.keys ? '  keys ' + JSON.stringify(x.keys) : ''}` +
               `${x.error ? '  ' + x.error : ''}${!x.keys && x.start ? '  ' + x.start.replace(/\s+/g, ' ') : ''}`);
  $('#probeText').value = lines.join('\n');
  $('#probe').hidden = false; $('#probeText').select();
}
async function test(cid, body) {
  try { await api(`/api/controllers/${cid}/test`, {method: 'POST', body: JSON.stringify(body)}); }
  catch (e) { toast(e.message); }
}
function esc(s) { return String(s).replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch])); }

let settingsLoaded = false;
async function refresh() {
  try { status = await api('/api/status'); } catch { return; }
  const rx = status.sacn, live = rx.active_universes.length > 0;
  $('#rxLamp').className = 'lamp ' + (live ? 'on' : rx.listening ? '' : 'bad');
  $('#rxText').textContent = rx.error ? rx.error
    : live ? `receiving ${rx.rate} packets/s${rx.sources.length ? ' from ' + rx.sources.join(', ') : ''}`
    : rx.listening ? `listening on UDP 5568${rx.multicast ? ', multicast joined: ' + (rx.joined.length ? rx.joined.join(' ') : 'none yet') : ', unicast'}` : 'not listening';
  $('#uniText').textContent = live ? `universes ${rx.active_universes.join(' ')}` : '';
  const dd = status.ddp;
  $('#ddpText').textContent = dd.error ? `DDP: ${dd.error}` : dd.active ? `DDP ${dd.rate} packets/s` : dd.enabled ? 'DDP listening on 4048' : 'DDP off';
  $('#fpsText').textContent = status.output.fps_actual;
  renderParameters(status.parameters);
  renderTestTargets();
  const ae = document.activeElement;                      // don't clobber an edit in progress (buttons don't count)
  if (ae && ae.closest('#ctrls') && ae.matches('input, select')) return;
  renderControllers();
  if (!settingsLoaded) {
    const s = status.settings;
    $('#sIface').value = s.sacn.interface_ip; $('#sMc').checked = s.sacn.multicast;
    $('#sCpu').value = s.sacn.channels_per_universe; $('#sIdle').value = s.sacn.idle_timeout_s; $('#sDdp').checked = s.ddp.enabled;
    $('#oFps').value = s.output.fps; $('#oGap').value = s.output.fragment_gap_ms;
    $('#oRe').value = s.output.rt_reassert_s; $('#oRestore').checked = s.output.restore_mode_on_idle;
    settingsLoaded = true;
  }
}
function renderParameters(p) {
  $('#paramText').textContent = p.parameters.toLocaleString();
  const box = $('#paramSummary');
  if (!p.controllers) { box.textContent = 'No maps yet, so no parameters to count. Pull a map on a controller.'; return; }
  const key = JSON.stringify(p);
  if (box.dataset.key === key) return;                     // unchanged: leave the DOM alone
  box.dataset.key = key;
  const span = us => !us.length ? '' : us.length === 1 ? `${us[0]}` : `${us[0]} to ${us[us.length - 1]}`;
  box.innerHTML = `Pixel map parameter count: <strong>${p.parameters.toLocaleString()}</strong> parameters
    from ${p.pixels.toLocaleString()} pixels on ${p.controllers} controller${p.controllers === 1 ? '' : 's'},
    ${p.universes} universe${p.universes === 1 ? '' : 's'}.
    <table><tr><th>Controller</th><th>Pixels</th><th>Per pixel</th><th>Parameters</th><th>Universes</th></tr>
    ${p.per_controller.map(c => `<tr><td>${esc(c.name)}</td><td>${c.pixels.toLocaleString()}</td><td>${c.bpp}</td>
      <td>${c.parameters.toLocaleString()}</td><td>${span(c.universes)}</td></tr>`).join('')}</table>`;
}
// ---------------------------------------------------------------- test patterns
let patterns = {};
function renderTestTargets() {
  const sel = $('#tTarget'), want = ['', ...status.controllers.map(c => c.id)].join('|');
  if (sel.dataset.key === want) return;
  const keep = sel.value; sel.dataset.key = want;
  sel.innerHTML = '<option value="">All enabled</option>' +
    status.controllers.map(c => `<option value="${esc(c.id)}">${esc(c.name)} (${esc(c.ip)})</option>`).join('');
  sel.value = [...sel.options].some(o => o.value === keep) ? keep : '';
  stepWhere();
}
function testTargets() {
  const id = $('#tTarget').value;
  return id ? status.controllers.filter(c => c.id === id) : status.controllers.filter(c => c.enabled);
}
function colourRGB() { const h = $('#tColour').value; return [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)); }
async function runTest(body) {
  const ids = testTargets().map(c => c.id);
  if (!ids.length) { $('#tMsg').textContent = 'No controllers'; return; }
  try { const r = await api('/api/test', {method: 'POST', body: JSON.stringify({ids, ...body})});
        $('#tMsg').textContent = body.mode === 'stop' ? 'Stopped' : `Running on ${r.controllers}`; }
  catch (e) { $('#tMsg').textContent = e.message; }
  refresh();
}
function stepWhere() {
  const c = testTargets()[0], i = +$('#tIndex').value;
  if (!c) { $('#tWhere').textContent = ''; return; }
  if (c.n_led) $('#tIndex').max = c.n_led - 1;
  const [port, led] = portOf(c, i);
  $('#tWhere').textContent = c.n_led && i >= c.n_led ? `past the last LED (${c.n_led - 1})` : `= port ${port}, LED ${led}${testTargets().length > 1 ? ' (on each controller)' : ''}`;
}
async function lightStep() {
  stepWhere();
  await runTest({mode: 'pixel', index: +$('#tIndex').value, rgb: colourRGB(), seconds: 30});
}
document.querySelectorAll('[data-step]').forEach(b => b.onclick = () => {
  const c = testTargets()[0], max = c && c.n_led ? c.n_led - 1 : 1e9;
  $('#tIndex').value = Math.max(0, Math.min(max, +$('#tIndex').value + +b.dataset.step)); lightStep();
});
$('#tIndex').onchange = lightStep;
$('#tTarget').onchange = stepWhere;
$('#tPattern').onchange = () => { $('#tDesc').textContent = patterns[$('#tPattern').value] || ''; };
$('#tStart').onclick = () => runTest({mode: 'pattern', pattern: $('#tPattern').value, rgb: colourRGB(),
                                      speed: +$('#tSpeed').value, seconds: +$('#tSecs').value});
$('#tStop').onclick = () => runTest({mode: 'stop', ids: status.controllers.map(c => c.id)});
api('/api/mapping/info').then(m => {
  const host = location.hostname === 'localhost' || location.hostname === '127.0.0.1' ? '<this PC\'s IP>' : location.hostname;
  $('#mapInfo').innerHTML = m.https_port
    ? `On the phone, open <strong>https://${esc(host)}:${m.https_port}/map</strong> and accept the certificate warning once.`
    : 'HTTPS is off (no certificate), and phones only allow the camera over HTTPS. Re-run install.sh / install.bat, then restart.';
}).catch(() => { $('#mapInfo').textContent = 'unavailable'; });
api('/api/patterns').then(p => {
  patterns = p;
  $('#tPattern').innerHTML = Object.keys(p).map(k => `<option value="${k}">${k}</option>`).join('');
  $('#tPattern').onchange();
});

async function autoPatch(mode) {
  try { const r = await api('/api/patch/auto', {method: 'POST', body: JSON.stringify({mode, start_universe: 1, order: 'group'})});
        toast(`Patched ${r.length} controllers (${mode})`); refresh(); }
  catch (e) { toast(e.message); }
}
$('#patchAligned').onclick = () => autoPatch('aligned');
$('#patchPacked').onclick = () => autoPatch('packed');

$('#addForm').onsubmit = async e => {
  e.preventDefault(); $('#addMsg').textContent = 'connecting';
  try {
    const r = await api('/api/controllers', {method: 'POST', body: JSON.stringify({ip: $('#addIp').value, universe: +$('#addU').value, channel: +$('#addC').value})});
    $('#addMsg').textContent = r.status === 'error' ? `Added, but could not connect: ${r.error}` : `Added ${r.name}: ${r.n_led} LEDs`;
    $('#addIp').value = '';
  } catch (err) { $('#addMsg').textContent = err.message; }
  refresh();
};
$('#saveSettings').onclick = async () => {
  try {
    await api('/api/settings', {method: 'POST', body: JSON.stringify({
      sacn: {interface_ip: $('#sIface').value || '0.0.0.0', multicast: $('#sMc').checked, channels_per_universe: +$('#sCpu').value, idle_timeout_s: +$('#sIdle').value},
      ddp: {enabled: $('#sDdp').checked},
      output: {fps: +$('#oFps').value, fragment_gap_ms: +$('#oGap').value, rt_reassert_s: +$('#oRe').value, restore_mode_on_idle: $('#oRestore').checked}})});
    $('#setMsg').textContent = 'Saved'; setTimeout(() => $('#setMsg').textContent = '', 2000);
  } catch (e) { $('#setMsg').textContent = e.message; }
};

// ---------------------------------------------------------------- live map
const canvas = $('#map'), ctx = canvas.getContext('2d');
async function loadMaps() { maps = (await api('/api/map')).filter(m => m.points.length); }
function layoutTiles() {
  const W = canvas.width, H = canvas.height, pad = 40;
  if (!maps.length) return [];
  if ($('#shared').checked) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const m of maps) for (const [x, y] of m.points) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    return maps.map(m => ({m, x: pad, y: pad, w: W - 2 * pad, h: H - 2 * pad, x0, x1, y0, y1, label: ''}));
  }
  const cols = Math.ceil(Math.sqrt(maps.length)), rows = Math.ceil(maps.length / cols);
  const tw = (W - pad) / cols, th = (H - pad) / rows;
  return maps.map((m, i) => {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const [x, y] of m.points) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    return {m, x: pad / 2 + (i % cols) * tw + 10, y: pad / 2 + Math.floor(i / cols) * th + 26, w: tw - 20, h: th - 40, x0, x1, y0, y1, label: m.name};
  });
}
function draw() {
  if (!maps.length) { canvas.hidden = true; $('#mapEmpty').hidden = false; return; }
  canvas.hidden = false; $('#mapEmpty').hidden = true;
  ctx.fillStyle = '#0f1218'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  hit = [];
  for (const t of layoutTiles()) {
    const m = t.m, sx = t.w / ((t.x1 - t.x0) || 1), sy = t.h / ((t.y1 - t.y0) || 1), s = Math.min(sx, sy);
    const ox = t.x + (t.w - (t.x1 - t.x0) * s) / 2, oy = t.y + (t.h - (t.y1 - t.y0) * s) / 2;
    const r = Math.max(1.5, Math.min(4, s * 0.004 * 2 + 1));
    if (t.label) { ctx.fillStyle = '#8c95a6'; ctx.font = '13px "IBM Plex Sans", system-ui'; ctx.fillText(t.label, t.x, t.y - 10); }
    const f = frames[m.id] ? Uint8Array.from(atob(frames[m.id]), ch => ch.charCodeAt(0)) : null;
    m.points.forEach(([x, y], i) => {
      const px = ox + (x - t.x0) * s, py = oy + (y - t.y0) * s;      // Twinkly y grows downward, same as canvas
      let col = '#2c3442';
      if (f) {
        const o = i * m.bpp;
        let R = 0, G = 0, B = 0;
        if (m.bpp === 4) { const w = f[o] || 0; R = Math.min(255, (f[o+1]||0) + w); G = Math.min(255, (f[o+2]||0) + w); B = Math.min(255, (f[o+3]||0) + w); }
        else { R = f[o] || 0; G = f[o+1] || 0; B = f[o+2] || 0; }
        if (R + G + B > 0) col = `rgb(${R},${G},${B})`;
      }
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.fill();
      hit.push({cid: m.id, c: m, index: i, x: px, y: py});
    });
  }
}
canvas.addEventListener('click', ev => {
  const rect = canvas.getBoundingClientRect();
  const x = (ev.clientX - rect.left) * canvas.width / rect.width, y = (ev.clientY - rect.top) * canvas.height / rect.height;
  let best = null, bd = 12 * 12;
  for (const h of hit) { const d = (h.x - x) ** 2 + (h.y - y) ** 2; if (d < bd) { bd = d; best = h; } }
  if (!best) return;
  const [port, led] = portOf(best.c, best.index);
  $('#pick').textContent = `${best.c.name}: index ${best.index}, port ${port}, LED ${led}`;
  test(best.cid, {mode: 'pixel', index: best.index, rgb: [255, 255, 255], seconds: 3});
});
$('#shared').onchange = draw;
async function pollFrames() {
  if (maps.length && !document.hidden) { try { frames = await api('/api/frames'); } catch {} draw(); }
  setTimeout(pollFrames, 100);
}

// ---------------------------------------------------------------- Eos export
function eosQuery() {
  const q = new URLSearchParams({pixelmap: $('#ePm').value, flatten: $('#eFlat').value, arrange: $('#eArr').value,
    width: $('#eWidth').value, mode: $('#eMode').value, channel_base: $('#eBase').value,
    manufacturer: $('#eMfr').value, fixture_type: $('#eType').value});
  return q.toString();
}
async function eosDownload(path) {
  const r = await fetch(path + (path.includes('?') ? '' : '?') + eosQuery());
  if (!r.ok) { const j = await r.json().catch(() => ({})); $('#eosMsg').textContent = j.error || `error ${r.status}`; return; }
  const blob = await r.blob(), a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = path.split('/').pop().split('?')[0]; a.click(); URL.revokeObjectURL(a.href);
  $('#eosMsg').textContent = 'Downloaded';
}
$('#eosMap').onclick = () => eosDownload('/api/export/eos_pixelmap.csv');
$('#eosPatch').onclick = () => eosDownload('/api/export/eos_patch.csv');
$('#eosPreview').onclick = () => window.open('/api/export/eos_preview.svg?' + eosQuery(), '_blank');

$('#probeClose').onclick = () => { $('#probe').hidden = true; };
$('#probeCopy').onclick = async () => {
  try { await navigator.clipboard.writeText($('#probeText').value); toast('Copied'); }
  catch { $('#probeText').select(); document.execCommand('copy'); toast('Copied'); }
};
refresh(); setInterval(refresh, 1000);
loadMaps().then(() => { draw(); pollFrames(); });
