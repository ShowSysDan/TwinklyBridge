// The guided walk's plan: pull guideStep() out of bridge/static/js/mapping.js (which needs a
// browser to load whole) and print what it chooses for each arc the page offers.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const m = src.match(/function guideStep\(arc, views\) \{[\s\S]*?\n\}/);
if (!m) { console.error('guideStep not found in ' + process.argv[2]); process.exit(2); }
const guideStep = new Function(m[0] + '; return guideStep;')();
const out = {};
for (const arc of [315, 250, 180, 120]) {
  const step = guideStep(arc, 8);
  const angles = [];
  for (let k = 0; k < 8; k++) angles.push(((k * step) % 360 + 360) % 360);
  out[arc] = {step, angles, span: angles[7],
              edgeOn: angles.filter(a => Math.min(Math.abs(a - 90), Math.abs(a - 270)) < 6).length};
}
console.log(JSON.stringify(out));
