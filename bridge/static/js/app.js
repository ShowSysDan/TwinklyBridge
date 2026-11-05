const $ = s => document.querySelector(s);
let status = null, maps = [], frames = {}, hit = [];      // hit: [{cid,index,x,y}] for click lookup
let toastT = null;

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2600);
}
async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts});
  const j = await r.json().catch(() => ({}));
  if (!r.ok && j.error) throw new Error(j.error);
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
    const state = c.testing ? 'test pattern' : c.status === 'streaming' ? 'streaming' : c.status;
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
        <button data-a="csv">Upload CSV</button>
        ${c.has_map ? `<button data-a="dl">Download CSV</button>` : ''}
        <button data-a="solid">Flash red</button>
        <button data-a="off">Blackout 3 s</button>
        <button data-a="connect">Reconnect</button>
        <button data-a="remove" class="quiet">Remove</button>
        <input type="file" accept=".csv,text/csv" hidden data-a="csvfile">
      </div>
      ${c.error ? `<div class="err">${esc(c.error)}</div>` : ''}`;
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
  if (document.activeElement && document.activeElement.closest('#ctrls')) return;   // don't clobber an edit in progress
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

refresh(); setInterval(refresh, 1000);
loadMaps().then(() => { draw(); pollFrames(); });
