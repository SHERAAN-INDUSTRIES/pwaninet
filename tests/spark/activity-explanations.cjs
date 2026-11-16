// Exercise the actual DOM renderer, hydration and theme/width behavior offline.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/js/pwanimate/activity-trail.js','utf8');
const a=src.indexOf('    const TOOL_LABELS'),b=src.indexOf('    class Trail',a);
const project=vm.runInNewContext('(function(){'+src.slice(a,b)+';return publicEvent;})()');
const secret='PRIVATE PROMPT <script>alert(1)</script>';
const fixtures=JSON.parse(fs.readFileSync('tests/spark/fixtures/activity-explanations.json','utf8'));
const payloads=fixtures.map(f=>({...f.input,text:secret,summary:secret,detail:secret,arguments:{query:secret}}));
const expected=fixtures;
for(let i=0;i<payloads.length;i++){
 const clean=project(payloads[i]);
 assert.equal(clean.summary,expected[i].summary);
 assert.ok(!JSON.stringify(clean).includes(secret));
 assert.equal(project(clean).summary,clean.summary);
}
assert.equal(project({kind:'stage',id:'stage-constructor'}),null);
assert.match(project({kind:'tool',tool:'constructor'}).summary,/checking the information/);
const teachStage=project({type:'activity',kind:'stage',id:'stage-teaching_check',status:'running',activity_type:'thinking'});
assert.equal(teachStage.text,'Preparing your lesson');
assert.match(teachStage.summary,/study session and lesson state/);
assert.equal(project({type:'activity',kind:'stage',id:'stage-teaching_check',status:'running',activity_type:'thinking'}).summary,teachStage.summary);
console.log('PASS backend/frontend explanation parity, replay idempotence, outcomes and injection filtering');
const fixture=String.raw`
try {
 const check=(condition,message)=>{if(!condition)throw Error(message);};
 const trail=SparkActivity.create(true); document.body.appendChild(trail.element);
 const start={type:'activity',kind:'tool',id:'tool-1',tool:'web_search',status:'running',text:'PRIVATE',summary:'<img src=x onerror=alert(1)>'};
 trail.add(start);
 check(trail.element.querySelector('.spark-activity-detail').textContent.includes('external sources'),'live purpose missing');
 trail.add({...start,status:'completed',result_count:3});
 check(trail.element.querySelectorAll('.spark-activity-step').length===1,'tool row duplicated');
 check(trail.element.querySelector('.spark-activity-detail').textContent.includes('3 result(s)'),'observed outcome missing');
 check(!trail.element.querySelector('img'),'untrusted summary rendered');
 trail.add({kind:'tool',id:'failed',tool:'retrieve_documents',status:'failed'});
 check(trail.element.querySelector('[data-eid="failed"] .spark-activity-detail').textContent.includes('could not complete'),'failure missing');
 trail.add({kind:'checklist',id:'checklist',items:[{status:'done'},{status:'blocked'}]});
 check(trail.element.querySelector('.spark-checklist .spark-activity-detail').textContent.includes('1 of 2'),'checklist summary missing');
 for(const width of [320,1200]) {
   document.body.style.width=width+'px';
   check(trail.element.scrollWidth<=width,'activity overflow at '+width);
 }
 const holder=document.createElement('div');holder.className='spark-activity-history';
 const data=document.createElement('script');data.type='application/json';
 data.textContent=JSON.stringify([{...start,status:'completed',result_count:3}]);holder.appendChild(data);document.body.appendChild(holder);
 SparkActivity.hydrate();
 check(document.querySelectorAll('.spark-activity-detail').length>=3,'history explanation missing');
 check(document.querySelectorAll('.spark-activity-step').length===3,'history/live rows incorrect');
 const result=document.createElement('pre');result.id='test-result';result.textContent='PASS live purpose, observed result, failure, checklist, safe text, mobile width and history hydration';document.body.appendChild(result);
} catch(error) {const result=document.createElement('pre');result.id='test-result';result.textContent='FAIL '+error.stack;document.body.appendChild(result);}
`;
new vm.Script(fixture);
if (process.env.SPARK_ACTIVITY_BROWSER_HTML) {
 const html='<!doctype html><meta charset="utf-8"><style>body{margin:0;--pwanimate-muted:#999;--pwanimate-asst-text:#eee;--pwanimate-border:#333;background:#111315}.spark-activity{max-width:100%}.spark-activity-detail{overflow-wrap:anywhere}</style><link rel="stylesheet" href="file://'+process.cwd()+'/static/css/pwanimate/pwanimate.css"><body><script>'+src+'</script><script>'+fixture+'</script>';
 fs.writeFileSync(process.env.SPARK_ACTIVITY_BROWSER_HTML,html);
 console.log('Wrote offline browser fixture');
}
const recalled=project({type:'activity',kind:'tool',id:'recall-1',tool:'recall_memory',status:'running',summary:secret});
assert.equal(recalled.text,'Reviewing your saved study history');
assert.match(recalled.summary,/approved memories and recorded academic activity across conversations/);
assert.ok(!JSON.stringify(recalled).includes(secret));
assert.equal(project({...recalled,status:'completed'}).tool,'recall_memory');
assert.equal(project({type:'activity',kind:'tool',tool:'send_notification',status:'running'}).text,'Creating your notification');
assert.match(project({type:'activity',kind:'stage',id:'stage-reading_document_page',status:'running'}).summary,/authorized page evidence/);
console.log('PASS saved-memory, notification and authorized-page activity projection');
