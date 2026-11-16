// Unified composer interactions: typed and mixed questions render as the
// upper section of the composer; the main composer submits the atomic answer
// through the same durable resume path. Choices-only, confirm and group
// wizards keep their standalone block. DOM adapter only — no server/provider.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/pwanimate/pwanimate-chat.js', 'utf8');

class Element {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = []; this.dataset = {}; this.disabled = false; this.hidden = false;
    this._classes = new Set(); this.listeners = {};
    this.textContent = ''; this.innerHTML = ''; this.value = ''; this.placeholder = '';
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._classes;
    return {
      add: (...c) => c.forEach(x => s.add(x)),
      remove: (...c) => c.forEach(x => s.delete(x)),
      contains: c => s.has(c),
    };
  }
  append(...nodes) { nodes.forEach(n => { if (n) { n.parent = this; this.children.push(n); } }); }
  appendChild(n) { this.append(n); return n; }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  after(n) { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, n); }
  setAttribute() {}
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(f => f !== fn); }
  click() { (this.listeners.click || []).forEach(f => f({ preventDefault() {} })); }
  matches(sel) {
    sel = String(sel).trim();
    if (sel.includes(',')) return sel.split(',').some(s => this.matches(s));
    if (sel.startsWith('.')) return this._classes.has(sel.slice(1));
    const m = sel.match(/^[a-zA-Z][\w-]*/);
    return m ? this.tagName === m[0].toUpperCase() : false;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = el => { for (const c of el.children) { if (c.matches && c.matches(sel)) out.push(c); if (c.children) walk(c); } };
    walk(this); return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  focus() {}
  remove() { this.removed = true; }
}

const begin = source.indexOf('        _interactionSignature()');
const end = source.indexOf('        // Mark the compact trail', begin);
assert.ok(begin > 0 && end > begin, 'interaction block methods not found');

const store = new Map();
const context = {
  document: { createElement: t => new Element(t), createDocumentFragment: () => new Element('fragment') },
  window: { sparkRenderIcons() {} },
  localStorage: {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
  },
};
vm.createContext(context);
const Chat = vm.runInContext('(class {' + source.slice(begin, end) + '})', context);

function makeChat(pi, inputValue = '') {
  const ui = new Chat();
  ui.renderMarkdownWithMathAndCode = s => String(s);
  ui.isGenerating = false;
  ui.calls = [];
  ui.resumeInteraction = async (...args) => { ui.calls.push(args); };
  ui.resetTextareaHeight = () => {};
  ui.showSystemNotice = () => {};
  ui.input = {
    value: inputValue, placeholder: 'Ask Spark anything',
    listeners: {},
    addEventListener(k, f) { (this.listeners[k] = this.listeners[k] || []).push(f); },
    removeEventListener(k, f) { this.listeners[k] = (this.listeners[k] || []).filter(x => x !== f); },
    dispatch(k) { (this.listeners[k] || []).forEach(f => f({})); },
  };
  ui.composerForm = { parentNode: { insertBefore(el) { this.inserted = el; } } };
  ui.waitingForInput = true;
  ui.pendingInteraction = pi;
  return ui;
}

const textPi = { run_id: 'run-1', interaction_id: 'iq-text', input_type: 'text', kind: 'clarification', prompt: 'Explain step 2.' };
const mixedPi = { run_id: 'run-2', interaction_id: 'iq-mixed', input_type: 'single_choice', kind: 'clarification', prompt: 'Pick a focus.', allow_custom_answer: true, options: [{ id: 'a', label: 'Row reduce' }, { id: 'b', label: 'Columns' }] };
const choiceOnlyPi = { run_id: 'run-3', interaction_id: 'iq-choice', input_type: 'single_choice', kind: 'clarification', prompt: 'Pick one.', allow_custom_answer: false, options: [{ id: 'a', label: 'A' }] };
const confirmPi = { run_id: 'run-4', interaction_id: 'iq-confirm', input_type: 'confirm', kind: 'approval', prompt: 'Proceed?' };

(async () => {
  // 1. Typed interaction merges into the composer surface.
  let ui = makeChat(textPi);
  ui._syncInteractionBlock();
  const merged = ui._interactionBlockEl;
  assert.ok(merged, 'merged block missing');
  assert.ok(merged.classList.contains('spark-interaction-block--merged'), 'merged class missing');
  assert.equal(ui.input.placeholder, 'Type your answer', 'composer placeholder not swapped');
  assert.equal(merged.querySelectorAll('button').find(b => b.textContent === 'Send'), undefined, 'in-panel Send must not render');
  assert.equal(merged.querySelector('textarea, input'), null, 'no second text input may render');
  assert.equal(JSON.stringify(merged._composerAnswer('because it cancels')), JSON.stringify({ text: 'because it cancels' }));
  assert.equal(merged._composerAnswer(''), null, 'empty typed answer rejected client-side');
  assert.equal(merged._composerAnswer('   '), null);

  // Composer draft round-trips through the interaction draft store.
  ui.input.value = 'half typed';
  ui.input.dispatch('input');
  const draft = JSON.parse(store.get('spark-interaction-draft:iq-text'));
  assert.equal(draft.composer_text, 'half typed', 'composer text missing from draft');
  ui._syncInteractionBlock(); // same signature: no rebuild
  ui.pendingInteraction = null; ui.waitingForInput = false; ui._syncInteractionBlock();
  assert.equal(ui.input.placeholder, 'Ask Spark anything', 'placeholder not restored');

  // Reload-style rebuild restores the typed draft into the composer.
  ui = makeChat(textPi);
  ui._syncInteractionBlock();
  assert.equal(ui.input.value, 'half typed', 'composer draft not restored');
  ui.pendingInteraction = null; ui.waitingForInput = false; ui._syncInteractionBlock();

  // 2. Mixed choice+text: selections live in the panel, text in the composer,
  //    submitted atomically through _composerAnswer.
  ui = makeChat(mixedPi);
  ui._syncInteractionBlock();
  const mixed = ui._interactionBlockEl;
  assert.ok(mixed.classList.contains('spark-interaction-block--merged'));
  const options = mixed.querySelectorAll('.spark-interaction-option');
  assert.equal(options.length, 2, 'choice options missing');
  assert.equal(mixed.querySelector('.spark-interaction-input'), null, 'no duplicate inline text input');
  options[0].click();
  assert.equal(JSON.stringify(mixed._composerAnswer('')), JSON.stringify({ selected: ['a'] }), 'selection-only answer wrong');
  assert.equal(JSON.stringify(mixed._composerAnswer('extra detail')), JSON.stringify({ selected: ['a'], text: 'extra detail' }), 'atomic mixed answer wrong');
  // Fresh interaction: typed text alone is the custom-answer path.
  ui = makeChat({ ...mixedPi, interaction_id: 'iq-mixed-2' });
  ui._syncInteractionBlock();
  assert.equal(JSON.stringify(ui._interactionBlockEl._composerAnswer('my own words')), JSON.stringify({ text: 'my own words' }));
  assert.equal(ui._interactionBlockEl._composerAnswer(''), null, 'empty mixed answer rejected');

  // 3. Choices-only interactions keep the standalone block with its own Send.
  ui = makeChat(choiceOnlyPi);
  ui._syncInteractionBlock();
  const standalone = ui._interactionBlockEl;
  assert.ok(!standalone.classList.contains('spark-interaction-block--merged'), 'choices-only must stay standalone');
  assert.equal(typeof standalone._composerAnswer, 'undefined');
  assert.ok(standalone.querySelectorAll('button').find(b => b.textContent === 'Send'), 'standalone Send missing');
  assert.equal(ui.input.placeholder, 'Ask Spark anything', 'placeholder untouched for standalone');

  // 4. Confirm interactions stay standalone with explicit decision buttons.
  ui = makeChat(confirmPi);
  ui._syncInteractionBlock();
  const confirm = ui._interactionBlockEl;
  assert.ok(!confirm.classList.contains('spark-interaction-block--merged'));
  assert.ok(confirm.querySelectorAll('button').find(b => b.textContent === 'Confirm'));
  assert.ok(confirm.querySelectorAll('button').find(b => b.textContent === 'Decline'));

  // 5. Composer listener cleanup on block removal.
  ui = makeChat(textPi);
  ui._syncInteractionBlock();
  const listenersBefore = ui.input.listeners.input.length;
  ui.pendingInteraction = null; ui.waitingForInput = false; ui._syncInteractionBlock();
  assert.equal(ui.input.listeners.input.length, listenersBefore - 1, 'composer draft listener leaked');

  // 6. Visual merge contract: shared composer surface, single seam, safe radii.
  const css = fs.readFileSync('static/css/pwanimate/pwanimate.css', 'utf8');
  assert.match(css, /\.spark-interaction-block--merged\{[^}]*background:var\(--pwanimate-composer-bg\)/);
  assert.match(css, /\.spark-interaction-block--merged\{[^}]*border-bottom:none/);
  assert.match(css, /\.spark-interaction-block--merged\{[^}]*border-radius:26px 26px 0 0/);
  assert.match(css, /\.spark-interaction-block--merged \+ #pwanimate-composer-form\{[^}]*border-radius:0 0 26px 26px/);

  console.log('PASS: merged composer panel for typed/mixed questions, atomic {selected,text} answers, standalone choices-only/confirm, draft restore, listener cleanup, unified surface CSS');
})().catch(e => { console.error(e); process.exitCode = 1; });
