// Validate existing literal placeholders against the actual committed vendor.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { icons, createIcons } = require('../../static/vendor/lucide/lucide.min.js');
const names = new Set();
function walk(dir) {
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const file = dir + '/' + entry.name;
    if (entry.isDirectory()) walk(file);
    else if (file.endsWith('.html')) {
      for (const match of fs.readFileSync(file, 'utf8').matchAll(/data-lucide="([^"{]+)"/g)) names.add(match[1]);
    }
  }
}
walk('pwanimate/templates');
walk('templates');
assert.equal(typeof createIcons, 'function');
for (const name of names) {
  const key = name.replace(/[-_\s]+(.)/g, (_, c) => c.toUpperCase()).replace(/^./, c => c.toUpperCase());
  assert.ok(Object.hasOwn(icons, key), 'Unavailable literal icon: ' + name);
}
console.log(`PASS ${names.size} literal template icon names; ${Object.keys(icons).length} registry keys`);
