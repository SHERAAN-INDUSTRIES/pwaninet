// Keep the browser SDK local, versioned and reproducible; no runtime CDN dependency.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = path.join(root, 'node_modules/livekit-client');
const dest = path.join(root, 'static/vendor/livekit');
fs.mkdirSync(dest, { recursive: true });
fs.copyFileSync(path.join(source, 'dist/livekit-client.esm.mjs'), path.join(dest, 'livekit-client.esm.mjs'));
fs.copyFileSync(path.join(source, 'LICENSE'), path.join(dest, 'LICENSE'));
fs.writeFileSync(path.join(dest, 'VERSION'), JSON.parse(fs.readFileSync(path.join(source, 'package.json'))).version + '\n');
