// Видимость зон по сессиям — тем же JS, что на странице карты зон (scripts/zone_map_template.html).
// DOM и lightweight-charts заменены заглушками; наружу отдаётся VIS.
// node vis_from_page.js ref.json out.json [индекс шага подгонки] [перевод серых]
const fs = require('fs');
const path = require('path');
const [ref, out, check = '0', prom = '3'] = process.argv.slice(2);
const tpl = fs.readFileSync(path.join(__dirname, '../../scripts/zone_map_template.html'), 'utf8');
const js = tpl.match(/<script>\s*([\s\S]*?)<\/script>\s*$/)[1]
  .replace('const v=VIS[lastS], shown', 'globalThis.__VIS=VIS; const v=VIS[lastS], shown')
  .replace('applyTheme(); recompute(); loop();', 'recompute();');   // без отрисовки
const data = fs.readFileSync(ref, 'utf8');

const el = () => new Proxy({ style: {}, dataset: {}, value: 0, max: 0, checked: false,
  setAttribute() {}, appendChild() {}, addEventListener() {},
  getBoundingClientRect: () => ({ width: 800, height: 600 }), getContext: () => new Proxy({}, { get: () => () => {} }) },
  { get: (t, k) => (k in t ? t[k] : undefined), set: (t, k, v) => { t[k] = v; return true; } });
const nodes = {};
globalThis.document = {
  getElementById: id => id === 'zone-data' ? { textContent: data } : (nodes[id] ||= el()),
  createElement: () => el(), querySelectorAll: () => [], documentElement: {},
};
globalThis.localStorage = { getItem: () => JSON.stringify({ check: +check, prom: +prom }), setItem() {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#000000' });
globalThis.matchMedia = () => ({ addEventListener() {} });
globalThis.MutationObserver = class { observe() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.window = globalThis;
const stub = new Proxy(function () {}, { get: () => stub, apply: () => stub });
globalThis.LightweightCharts = { createChart: () => stub, CrosshairMode: {} };

new Function(js)();
const D = JSON.parse(data);
const vis = globalThis.__VIS.map(v => v.list.map(x => [x.lo, x.hi, x.z.av, x.kind]));
const NAME = { hvn_S: 'HVN_S', hvn_R: 'HVN_R', hvn_range: 'HVN_RANGE', hvn_new: 'HVN_NEW', old_balance: 'OLD_BALANCE', grey: 'GREY' };
fs.writeFileSync(out, JSON.stringify(vis.map(l => l.map(x => [x[0], x[1], x[2], NAME[x[3]]]))));
console.log('сессий', vis.length, 'зон в последней', vis[vis.length - 1].length, 'шаг', D.steps[+check]);
