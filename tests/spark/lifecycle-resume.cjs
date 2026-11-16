const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const slice=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const methods=slice('        _acceptInteraction(', '        _clearRunState()')+
 slice('        async readChatEventStream(', '        removeTypingIndicator()')+
 slice('        async _resumeChatPayload(', '        // ------------------------------------------------- proposed actions');
const drafts=new Map(),context={AbortController,TextDecoder,Uint8Array,localStorage:{removeItem:k=>drafts.delete(k)},getCsrfToken:()=>''};
const C=vm.runInNewContext('(class {'+methods+'})',context);
function make(){
 const c=new C();c.pendingInteraction={run_id:'original',interaction_id:'question'};c.waitingForInput=true;
 c.block={removed:false};c.answers={selected:['chosen']};c.rows=0;c.events=[];
 c.setGenerating=v=>c.isGenerating=v;
 c.setWaitingForInput=v=>{c.waitingForInput=v;if(!v)c.block.removed=true;};
 c._markClarificationResolved=()=>{};
 c._clearRunState=()=>{c.pendingInteraction=null;c.activeRunId=null;c.setWaitingForInput(false);};
 c.appendAssistantMessage=()=>{c.rows++;return{contains:()=>false};};
 c.ensureActivityTrail=()=>({add:e=>c.events.push(e)});
 for(const name of ['showTypingIndicator','removeTypingIndicator','showSystemNotice','adoptActivity','scrollToMessageStart','refreshConversationsList','updateThinkingStatus'])c[name]=()=>{};
 drafts.set('spark-interaction-draft:question','saved');return c;
}
const sse=(events,onRead)=>{
 let i=0;return{ok:true,headers:{get:()=> 'text/event-stream'},status:200,body:{getReader:()=>({read:async()=>{onRead?.(i);return i<events.length?{value:new TextEncoder().encode(events[i++]),done:false}:{done:true};},cancel:async()=>{}})}};
};
const acceptance='event: interaction_accepted\ndata: {"run_id":"original","interaction_id":"question"}\n\n';
const oldQuestion='event: activity\ndata: {"type":"activity","kind":"clarification","id":"old","run_id":"original","interaction_id":"question","status":"waiting","text":"Again?"}\n\n';
const final='event: response\ndata: {"status_code":200,"data":{"message_id":42,"answer":"Done","activity":[]}}\n\n';
(async()=>{
 // Remove on acceptance before the final response, not when generation ends.
 let c=make(),posts=0;
 context.fetch=async(url,options)=>{assert.equal(url,'/api/pwanimate/chat/');assert.equal(JSON.parse(options.body).resume_run_id,'original');posts++;return sse([acceptance,oldQuestion,final],i=>{if(i===1){assert.equal(c.block.removed,true);assert.equal(c.isGenerating,true);assert.equal(drafts.has('spark-interaction-draft:question'),false);}});};
 const payload={resume_run_id:'original',interaction_id:'question',answer:c.answers};
 const first=c._resumeChatPayload(payload,null);await assert.rejects(c._resumeChatPayload(payload,null),/already processing/);await first;
 assert.equal(posts,1);assert.equal(c.rows,1);assert.equal(c.events.length,0);assert.equal(c.pendingInteraction,null);
 // If transport fails after commit, query status and reconnect with GET only.
 c=make();let routes=[];
 context.fetch=async(url,options)=>{routes.push([url,options.method||'GET']);if(options.method==='POST')throw Error('stream interrupted');if(url.endsWith('/stream/'))return sse([oldQuestion,final]);return{ok:true,json:async()=>({id:'original',status:'running',resolved_interaction_ids:['question']})};};
 await c._resumeChatPayload(payload,null);
 assert.equal(routes.filter(x=>x[1]==='POST').length,1);
 assert.equal(routes.at(-1)[0],'/api/pwanimate/agent-runs/original/stream/');
 assert.equal(c.rows,1);assert.equal(c.pendingInteraction,null);assert.equal(c.block.removed,true);
 // The original trail survives showTypingIndicator resetting its pointer.
 c=make();const oldRow={removed:false,remove(){this.removed=true;},querySelector:()=>({textContent:'',classList:{contains:()=>false}})};
 const trail={element:{open:false,closest:()=>oldRow,scrollIntoView(){}},finish(){}};
 c.currentActivityTrail=trail;c.showTypingIndicator=()=>{c.currentActivityTrail=null;};
 c.adoptActivity=row=>{trail.element.closest=()=>row;row.contains=el=>el===trail.element;};
 context.fetch=async()=>sse([acceptance,final]);
 await c._resumeChatPayload(payload,null);
 assert.equal(c.currentActivityTrail,trail);assert.equal(oldRow.removed,true);
 // Validation failures preserve the original question/selections and remain retryable.
 c=make();context.fetch=async()=>({headers:{get:()=> 'application/json'},status:400,json:async()=>({error:'Required answer'})});
 await assert.rejects(c._resumeChatPayload(payload,null),/Required answer/);
 assert.equal(c.block.removed,false);assert.equal(c.waitingForInput,true);assert.equal(c.answers.selected[0],'chosen');
 // Re-paused teaching keeps its segment and refreshes canonical state once.
 c=make();let refreshes=0;
 c.refreshStudyContext=async()=>{refreshes++;};c._trackRunState=()=>{};
 context.fetch=async()=>sse(['event: response\ndata: '+JSON.stringify({status_code:200,data:{answer:'Lesson segment',message_id:77,metadata:{teaching:{session_id:'session'},awaiting_clarification:true}}})+'\n\n']);
 await c._resumeChatPayload(payload,null);
 assert.equal(c.rows,1);assert.equal(c.waitingForInput,true);assert.equal(refreshes,1);
 console.log('PASS acceptance removes block before completion; original-run payload; rapid-click lock; one final response; interrupted SSE reconnects via GET; failed validation preserves answers');
})().catch(e=>{console.error(e);process.exitCode=1;});
