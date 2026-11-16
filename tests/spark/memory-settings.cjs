const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.dataset = {}; this.checked = false; this.disabled = false; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
}
const controls = Object.fromEntries(['status', 'enabled', 'automatic', 'memories'].map(key => [key, new Element()]));
const root = {dataset: {}, getClientRects: () => [1],
  querySelector: selector => controls[selector.match(/data-study="([^"]+)"/)?.[1]] || null};
let memory = {enabled: false, automatic_extraction: false, facts: []}, fail = false;
const requests = [];
const context = {window: {}, document: {readyState: 'complete', cookie: '', addEventListener() {},
  querySelector: () => null, querySelectorAll: () => [root], createElement: () => new Element()},
  fetch: async (url, options) => {
    assert.equal(url, '/api/pwanimate/memory/');
    if (options.method === 'PATCH') {
      const body = JSON.parse(options.body); requests.push(body);
      if (fail) return {ok: false, json: async () => ({detail: 'Consent changed'})};
      memory = {...memory, ...body};
      if (body.enabled === false) memory.automatic_extraction = false;
    }
    return {ok: true, json: async () => memory};
  }};
vm.runInNewContext(fs.readFileSync('static/js/spark-study-desk.js', 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await flush();
  assert.equal(controls.enabled.checked, false);
  assert.equal(controls.automatic.checked, false);
  assert.equal(controls.automatic.disabled, true);
  controls.enabled.checked = true;
  await controls.enabled.onchange({target: controls.enabled});
  assert.deepEqual(requests, [{enabled: true}]); // Enabling personal memory grants no academic consent.
  assert.equal(controls.automatic.checked, false);
  assert.equal(controls.automatic.disabled, false);
  controls.automatic.checked = true;
  await controls.automatic.onchange({target: controls.automatic});
  assert.deepEqual(requests[1], {automatic_extraction: true});
  assert.equal(controls.automatic.checked, true);
  fail = true;
  controls.automatic.checked = false;
  await controls.automatic.onchange({target: controls.automatic});
  assert.equal(controls.automatic.checked, true); // Failed requests never imply changed consent.
  assert.equal(controls.status.textContent, 'Consent changed');
  fail = false;
  memory.facts = [{id: 'academic', scope: 'academic', category: 'episodic', status: 'approved', text: 'Completed calculus'}];
  controls.enabled.checked = false;
  await controls.enabled.onchange({target: controls.enabled});
  assert.equal(controls.automatic.checked, false);
  assert.equal(controls.automatic.disabled, true);
  assert.match(controls.memories.children[0].children[0].textContent, /Academic memory/);
  assert.ok(requests.every(body => !('resume_run_id' in body) && !('query' in body)));
  console.log('PASS memory settings: separate consent, default off, disable cascade, failed consent rollback, academic label, no chat/run submission');
})().catch(error => { console.error(error); process.exitCode = 1; });
