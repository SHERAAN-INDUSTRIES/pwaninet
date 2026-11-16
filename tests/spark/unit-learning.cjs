const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.disabled=false;this.value='';}
 append(...items){this.children.push(...items);} appendChild(item){this.append(item);return item;}
 replaceChildren(...items){this.children=items;} setAttribute(){} getClientRects(){return[1];}
 addEventListener(type,fn){this['on'+type]=fn;} remove(){this.removed=true;}
 querySelectorAll(selector){return this.children.flatMap(child=>[...(selector.startsWith('.')?child.className?.split(' ').includes(selector.slice(1))?[child]:[]:child.tagName===selector?[child]:[]),...child.querySelectorAll(selector)]);}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const start=source.indexOf('        async refreshAcademicTracking()'),end=source.indexOf('        async refreshConversationsList()',start);
const context={document:{createElement:tag=>new Element(tag)},getCsrfToken:()=>'',CSS:{escape:s=>s}};
const Chat=vm.runInNewContext('(class {'+source.slice(start,end)+'})',context);
const button=(box,label)=>box.querySelectorAll('button').find(item=>item.textContent===label);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const chat=new Chat();let requests=[],resolve;
 context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return new Promise(done=>resolve=done);};
 let box=chat._academicTrackingBlock({id:'approval',summary:'Track Linear Algebra?'});
 const pending=button(box,'Start Tracking').onclick();button(box,'Start Tracking').onclick();
 assert.equal(requests.length,1);assert.deepEqual(requests[0].body,{decision:'confirm'});
 assert.equal(requests[0].url,'/api/pwanimate/actions/approval/');
 resolve({ok:true,json:async()=>({action:{status:'confirmed'}})});await pending;assert.equal(box.removed,true);
 context.fetch=async(url,options)=>{assert.deepEqual(JSON.parse(options.body),{decision:'cancel'});return{ok:true,json:async()=>({})};};
 box=chat._academicTrackingBlock({id:'approval'});await button(box,'Not Now').onclick();assert.equal(box.removed,true);
 context.fetch=async()=>({ok:false,json:async()=>({error:'Tracking consent changed'})});
 box=chat._academicTrackingBlock({id:'approval'});await button(box,'Start Tracking').onclick();
 assert.ok(!box.removed);assert.equal(button(box,'Start Tracking').disabled,false);
 chat.conversationId='current';chat.transcript=new Element();
 context.fetch=async()=>({ok:true,json:async()=>({pending_actions:[{id:'saved',summary:'Restored approval'}],attribution:null})});
 await chat.refreshAcademicTracking();assert.equal(chat.transcript.children.length,1);
 context.fetch=async()=>({ok:true,json:async()=>({pending_actions:[],attribution:{ambiguous:[{candidates:[{unit_name:'Linear Algebra'},{unit_name:'Geometry'}]}]}})});
 await chat.refreshAcademicTracking();assert.match(chat.transcript.children.at(-1).children[0].textContent,/Nothing has been attributed/);
 assert.ok(requests.every(item=>!('resume_run_id' in item.body)&&!('query' in item.body)));

 const controls=Object.fromEntries(['mode','status','enrollments','records','history'].map(name=>[name,new Element()]));
 const root=new Element();root.querySelector=selector=>controls[selector.match(/data-unit="([^"]+)"/)?.[1]]||null;
 let mode='ask',calls=[];
 const widget={window:{confirm:()=>true},document:{readyState:'complete',cookie:'',addEventListener(){},
   querySelector:()=>null,querySelectorAll:()=>[root],createElement:tag=>new Element(tag)},
   fetch:async(url,options)=>{
     calls.push({url,options});if(options.body)mode=JSON.parse(options.body).mode;
     if(url.endsWith('/academic/'))return{ok:true,json:async()=>({tracking:{mode},enrollments:[],records:[{id:'record',name:'Linear Algebra',term:'Semester 1'}]})};
     return{ok:true,json:async()=>({unit_name:'Linear Algebra',term:'Semester 1',read_only:true,topics:['matrices'],
       activities:[],assessments:[],sessions:[{id:'session',status:'completed',started_at:'2026-11-01T10:00:00Z'}]})};
   }};
 vm.runInNewContext(fs.readFileSync('static/js/spark-unit-learning.js','utf8'),widget);await tick();
 assert.equal(controls.mode.value,'ask');
 controls.mode.value='disabled';await controls.mode.onchange();assert.equal(mode,'disabled');
 assert.deepEqual(JSON.parse(calls.find(call=>call.options.body).options.body),{mode:'disabled'});
 await button(controls.records,'View study history').onclick();
 assert.match(controls.history.children[0].children[0].textContent,/Read-only history/);
 assert.equal(controls.history.querySelectorAll('button').length,0);
 assert.ok(calls.every(call=>!call.url.includes('/chat/')));
 console.log('PASS unit tracking UI: explicit consent/rejection, duplicate-click guard, stale error, restored requests, ambiguity, independent preference, read-only history, no run/chat submission');
})().catch(error=>{console.error(error);process.exitCode=1;});
