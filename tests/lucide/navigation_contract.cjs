// Reviewed WP2 mapping inventory, checked against the actual vendor, not a CDN catalog.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {icons} = require('../../static/vendor/lucide/lucide.min.js');
const inventory = require('./shared-navigation-mappings.json');
const key = name => name.replace(/[-_\s]+(.)/g, (_, c) => c.toUpperCase()).replace(/^./, c => c.toUpperCase());
let count = 0;
for (const [file, mappings] of Object.entries(inventory)) {
  const source = fs.readFileSync(file, 'utf8');
  const expected = {};
  for (const {lucide, count: total} of Object.values(mappings)) {
    assert.ok(Object.hasOwn(icons, key(lucide)), file + ': unknown name ' + lucide);
    expected[lucide] = (expected[lucide] || 0) + total;
    count += total;
  }
  for (const [name, minimum] of Object.entries(expected)) {
    const actual = source.split('data-lucide="' + name + '"').length - 1;
    assert.ok(actual >= minimum, `${file}: expected ${minimum} ${name} placeholders, got ${actual}`);
  }
  if (file !== 'templates/base.html') assert.ok(!/\bbi-[a-z]/.test(source), file + ': Bootstrap placeholder remains');
  for (const tag of source.matchAll(/<i\b[^>]*data-lucide="[^"{]+"[^>]*>/g)) {
    assert.ok(tag[0].includes('aria-hidden="true"'), 'Missing decorative aria-hidden: ' + tag[0]);
    assert.ok(!/\bbi-/.test(tag[0]), 'Competing icon classes: ' + tag[0]);
  }
}
const base = fs.readFileSync('templates/base.html', 'utf8');
const guestMap = vm.runInNewContext('(' + base.match(/var guestIconNames = (\{[\s\S]*?\});/)[1] + ')');
for (const name of Object.values(guestMap)) assert.ok(Object.hasOwn(icons, key(name)), name);
// Existing app callers retain their Bootstrap-name API; validate coverage without editing them.
function checkGuestCallers(dir) {
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const file = dir + '/' + entry.name;
    if (entry.isDirectory()) checkGuestCallers(file);
    else if (file.endsWith('.js') && !file.endsWith('.min.js')) {
      for (const match of fs.readFileSync(file, 'utf8').matchAll(/showGuestAuthPrompt\(\{[\s\S]*?icon:\s*'([^']+)'/g)) {
        assert.ok(Object.hasOwn(guestMap, match[1]), file + ': unmapped guest icon ' + match[1]);
      }
    }
  }
}
checkGuestCallers('static/js');
assert.ok(base.includes("{% static 'css/bootstrap-icons.css' %}"), 'Bootstrap dependency removed');
assert.ok(base.includes("{% static 'css/vasto-navigation-icons.css' %}"));
console.log(`PASS ${count} reviewed icon replacements, ${Object.keys(guestMap).length} dynamic guest mappings, accessibility and Bootstrap coexistence`);
