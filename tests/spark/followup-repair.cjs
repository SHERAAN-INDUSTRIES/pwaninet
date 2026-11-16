const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/pwanimate/pwanimate-chat.js', 'utf8');
const start = source.indexOf("const followUpId = btn.dataset.followUpId;", source.indexOf('// Wire up follow-up link clicks'));
const end = source.indexOf('\n                });', start);
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const body = source.slice(start, end);
async function scenario(status, data, fail=false) {
  const classes = new Set();
  const btn = {dataset:{followUpId:'id'},disabled:false,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)}};
  let calls=0, loads=0;
  const ctx = {conversationId:'conversation',loadConversation(){loads++;}};
  const run = new AsyncFunction('btn','fetch','getCsrfToken','alert',body);
  const fetch = async (_, options) => { calls++; assert.equal(JSON.parse(options.body).conversation_id,'conversation'); if(fail) throw Error(); return {status,ok:status<300,json:async()=>data}; };
  const a=run.call(ctx,btn,fetch,()=>'',()=>{});
  const b=run.call(ctx,btn,fetch,()=>'',()=>{});
  await Promise.all([a,b]);
  assert.equal(calls,1);
  return {btn,loads,classes};
}
(async()=>{
  let r=await scenario(200,{executed:true}); assert.equal(r.loads,1); assert(r.classes.has('consumed'));
  r=await scenario(202,{status:'executing'}); assert.equal(r.btn.disabled,false); assert.match(r.btn.textContent,/In progress/);
  r=await scenario(503,{error:'retry'}); assert.equal(r.btn.disabled,false);
  r=await scenario(410,{error:'expired'}); assert.equal(r.btn.disabled,true);
  r=await scenario(503,{},true); assert.equal(r.btn.disabled,false);
  console.log('PASS follow-up click, duplicate suppression, visible reload, in-progress, retry and expiry');
})().catch(e=>{console.error(e);process.exitCode=1;});
