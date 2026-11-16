const fs=require('node:fs');const assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const start=source.indexOf('        async refreshStudyContext() {');
const end=source.indexOf('\n        _showAcademicPreflight',start);
const code=source.slice(start,end).replace('async refreshStudyContext() {','async function() {').trim();
const refresh=eval('('+code+')');
class Element {constructor(){this.children=[];this.dataset={};this.disabled=false;}appendChild(child){this.children.push(child);return child;}remove(){this.removed=true;}}
global.document={createElement:()=>new Element(),getElementById:()=>null};global.getCsrfToken=()=>'';
let alerts=[];global.alert=t=>alerts.push(t);
(async()=>{
 const root=new Element();const ctx={conversationId:'chat',workspace:{querySelectorAll:()=>[]},transcript:root,refreshStudyContext:async()=>{},syncStudyModeUI(){this.synced=true;},pendingInteraction:{interaction_id:'parked'},setWaitingForInput(waiting,interaction){this.waitingForInput=waiting;if(interaction)this.pendingInteraction=interaction;}};
 let posts=[];global.fetch=async(url,options={})=>{
  if(options.method==='POST'){posts.push(JSON.parse(options.body));return {ok:false,json:async()=>({error:'stale generation'})};}
  return {ok:true,json:async()=>({ambiguous:true,generation:7,candidates:[{id:'a',status:'active',unit:'Algebra'},{id:'b',status:'paused',unit:'Algorithms'}]})};
 };
 await refresh.call(ctx);
 const buttons=root.children[0].children.filter(n=>n.onclick);assert.equal(buttons.length,2);
 await buttons[0].onclick();assert.deepEqual(posts[0],{session_id:'a',generation:7});assert.equal(buttons[0].disabled,false);assert.equal(alerts[0],'stale generation');
 global.fetch=async()=>{throw Error('offline');};await buttons[1].onclick();assert.equal(buttons[1].disabled,false);
 global.fetch=async url=>({ok:true,json:async()=>url.includes('study-context')?{ambiguous:false,session_id:'a',status:'paused',tracking:'untracked'}:{study_session:{id:'a',status:'paused',context_state:{resources:[]}}}});
 await refresh.call(ctx);assert.equal(ctx.activeStudySession.id,'a');assert.equal(ctx.studyModeActive,false);assert.equal(ctx.synced,true);assert.equal(ctx.pendingInteraction,null);assert.equal(ctx.waitingForInput,false);assert.equal(ctx.activeRunId,null);
 global.fetch=async url=>({ok:true,json:async()=>url.includes('study-context')?{session_id:'a',status:'active',tracking:'authorized',pending_run_id:'run-a',pending_interaction:{interaction_id:'question-a'}}:{study_session:{id:'a',status:'active'}}});
 await refresh.call(ctx);assert.equal(ctx.waitingForInput,true);assert.equal(ctx.pendingInteraction.interaction_id,'question-a');assert.equal(ctx.activeRunId,'run-a');
 global.fetch=async url=>({ok:true,json:async()=>url.includes('study-context')?{session_id:'a',status:'paused',tracking:'authorized',pending_run_id:'run-a',pending_interaction:{interaction_id:'question-a'}}:{study_session:{id:'a',status:'paused'}}});
 await refresh.call(ctx);assert.equal(ctx.waitingForInput,false);
 let release;
 const pending = new Promise(resolve=>release=resolve);
 const switched={conversationId:'old',workspace:{querySelectorAll:()=>[]},transcript:new Element(),
  syncStudyModeUI(){this.synced=true;},setWaitingForInput(){}};
 global.fetch=async()=>({ok:true,json:async()=>pending});
 const request=refresh.call(switched);
 await Promise.resolve();switched.conversationId='new';release({session_id:'old-session',generation:1});
 await request;assert.equal(switched.activeStudySession,undefined);
 // A stale generation cannot overwrite a newer selection in the same tab.
 const newer={conversationId:'chat',_studyContextVersion:{id:'chat',generation:8},
  activeStudySession:{id:'newer'},workspace:{querySelectorAll:()=>[]},transcript:new Element(),
  setWaitingForInput(){},syncStudyModeUI(){}};
 global.fetch=async()=>({ok:true,json:async()=>({session_id:'older',generation:7})});
 await refresh.call(newer);assert.equal(newer.activeStudySession.id,'newer');
 // An authoritative no-session response clears old header state.
 global.fetch=async()=>({ok:true,json:async()=>({session_id:null,generation:9,ambiguous:false})});
 await refresh.call(newer);assert.equal(newer.activeStudySession,null);assert.equal(newer.studySessionId,null);assert.equal(newer.studyModeActive,false);
 console.log('PASS canonical handoff choices, generation submission, stale/network retry and authoritative paused UI');
})().catch(error=>{console.error(error);process.exitCode=1;});

