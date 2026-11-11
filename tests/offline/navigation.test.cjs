const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/offline-pages-worker.js','utf8');
const body = source.slice(source.indexOf('    async function navigate(request)'),source.indexOf('    async function saveResource'));
function setup(online, fetch) {
  const context = { self:{navigator:{onLine:online}}, URL, Request, Response,
    state:async()=>({owner:'1',generation:1}), saved:async()=>new Response('saved'),
    offlinePage:async()=>new Response('offline'), offlineFetch:fetch };
  vm.createContext(context);vm.runInContext(body+';this.navigate=navigate;',context);return context;
}
test('saved hint cannot replace an online page, even after 1.5 seconds', async()=>{
  let fetched; const c=setup(true,async request=>{fetched=request.url;await new Promise(r=>setTimeout(r,1700));return new Response('live');});
  assert.equal(await (await c.navigate(new Request('https://example.com/chat/?offline_saved=1'))).text(),'live');
  assert.equal(fetched,'https://example.com/chat/');
});
test('online server errors remain server errors',async()=>{
  const c=setup(true,async()=>new Response('server error',{status:503}));
  assert.equal((await c.navigate(new Request('https://example.com/'))).status,503);
});
test('online connection failure does not substitute a snapshot',async()=>{
  const c=setup(true,async()=>{throw new TypeError('network failure')});
  await assert.rejects(c.navigate(new Request('https://example.com/')),/network failure/);
});
test('offline uses a saved page without a network request',async()=>{
  const c=setup(false,async()=>{throw Error('must not fetch')});
  assert.equal(await (await c.navigate(new Request('https://example.com/'))).text(),'saved');
});
test('connection lost during navigation permits offline fallback',async()=>{
  const c=setup(true,async()=>{c.self.navigator.onLine=false;throw Error('lost connection')});
  assert.equal(await (await c.navigate(new Request('https://example.com/'))).text(),'saved');
});
async function launch(online) {
  let destination;
  const context = {URL, URLSearchParams, navigator:{onLine:online}, setTimeout(){},
    location:{search:'',replace(value){destination=value}}, window:{Capacitor:{Plugins:{OfflineStore:{status:async()=>({origin:'https://example.com',snapshotRoute:'/chat/?offline_saved=1'})}}}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('www/launch.js','utf8'),context);
  await context.window.showVastoLaunchState({returning:true});return destination;
}
test('native startup opens the live route online, removing a persisted offline flag',async()=>{
  assert.equal(await launch(true),'https://example.com/chat/');
});
test('native startup can request the saved route only while offline',async()=>{
  assert.equal(await launch(false),'https://example.com/chat/?offline_saved=1');
});
