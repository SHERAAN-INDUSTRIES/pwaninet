const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const slice=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const methods=slice('        async _resumeChatPayload(', '        // ------------------------------------------------- proposed actions');
const context={AbortController,getCsrfToken:()=>''};
const C=vm.runInNewContext('(class {'+methods+'})',context);
const chat=new C();let rows=0,modes=0,posted;
chat.pendingInteraction={run_id:'original',interaction_id:'old'};
for(const method of ['showTypingIndicator','removeTypingIndicator','showSystemNotice','scrollToMessageStart','refreshConversationsList','updateThinkingStatus'])chat[method]=()=>{};
chat.setGenerating=v=>chat.isGenerating=v;
chat.setWaitingForInput=v=>chat.waitingForInput=v;
chat._acceptInteraction=()=>{chat.pendingInteraction=null;};
chat._trackRunState=data=>{chat.activeRunId=data.run.id;};
chat.renderTeachingMode=(row,teaching)=>{modes++;assert.equal(teaching.mode,'GUIDED');};
chat.appendAssistantMessage=answer=>{rows++;assert.equal(answer,'A justified worked step.');return{contains:()=>false};};
chat.adoptActivity=(row,activity,waiting)=>assert.equal(waiting,true);
context.fetch=async(url,options)=>{posted=JSON.parse(options.body);return{status:200,headers:{get:()=> 'application/json'},json:async()=>({answer:'A justified worked step.',metadata:{awaiting_clarification:true,teaching:{mode:'GUIDED'}},run:{id:'original'},activity:[]})};};
(async()=>{
 await chat._resumeChatPayload({resume_run_id:'original',interaction_id:'old',answer:{selected:['continue']}});
 assert.equal(posted.resume_run_id,'original');assert.equal(rows,1);assert.equal(modes,1);assert.equal(chat.activeRunId,'original');
 assert.equal(chat.isGenerating,false);assert.equal(chat.pendingInteraction,null);
 // Both initial and resumed teaching explanations use the existing sanitized renderer.
 assert.match(source,/awaitingInput && !data\.metadata\?\.teaching \? '' : data\.answer/);
 assert.match(source,/method:'PATCH'/);
 console.log('PASS teaching explanation remains visible while original run waits on its next single question');
})().catch(error=>{console.error(error);process.exitCode=1;});
