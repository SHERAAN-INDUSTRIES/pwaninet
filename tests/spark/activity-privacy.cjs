const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/js/pwanimate/activity-trail.js','utf8');
const a=src.indexOf('    const TOOL_LABELS'),b=src.indexOf('    class Trail',a);
const sanitize=vm.runInNewContext('(function(){'+src.slice(a,b)+';return publicEvent;})()');
const secret='PRIVATE PROMPT password=secret';
for(const kind of ['thinking','say','plan','reasoning'])assert.equal(sanitize({kind,text:secret}),null);
const tool=sanitize({kind:'tool',tool:'web_search',text:secret,detail:secret,summary:secret,status:'completed'});
assert.equal(tool.text,'Searching the web — complete');assert.equal(tool.detail,'');assert.match(tool.summary,/tool returned a result/);assert.ok(!JSON.stringify(tool).includes(secret));
const checklist=sanitize({kind:'checklist',text:secret,items:[{label:secret,finding:secret,status:'done'}]});
assert.ok(!JSON.stringify(checklist).includes(secret));
assert.equal(sanitize({kind:'stage',id:'stage-writing_answer',text:secret}).text,'Putting the answer together');
// A replayed final message returns the existing durable row before rendering.
const chat=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
let start=chat.indexOf('        appendAssistantMessage('),end=chat.indexOf('            const parsedHtml',start);
const C=vm.runInNewContext('(class {'+chat.slice(start,end)+'return row;}})',{document:{createElement:()=>({dataset:{}})}});
const c=new C(),row={dataset:{messageId:'42'}};c.transcript={querySelectorAll:()=>[row]};
assert.equal(c.appendAssistantMessage('Repeated answer',[],[],null,null,[],42),row);
console.log('PASS legacy activity narration/privacy, fixed summaries, checklist filtering, durable final-message deduplication');
