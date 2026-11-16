const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const start=source.indexOf('        async readChatEventStream('),end=source.indexOf('        removeTypingIndicator()',start);
const C=vm.runInNewContext('(class {'+source.slice(start,end)+'})',{TextDecoder,Uint8Array});
(async()=>{
 for(const withCallback of [true,false]){
  const chat=new C();let finished=0,calls=0;
  chat.currentActivityTrail={element:{open:true},finish(){finished++;}};
  const chunks=['event: chunk\ndata: {"text":"Hello"}\n\n','event: chunk\ndata: {"text":" again"}\n\n','event: response\ndata: {"status_code":200,"data":{}}\n\n'];
  let i=0;const reader={read:async()=>{if(i===1){assert.equal(chat.currentActivityTrail.element.open,false);chat.currentActivityTrail.element.open=true;}return{value:new TextEncoder().encode(chunks[i++]),done:false};},cancel:async()=>{}};
  await chat.readChatEventStream({body:{getReader:()=>reader}},withCallback?()=>{calls++;}:null);
  assert.equal(finished,1);assert.equal(chat.currentActivityTrail.element.open,true);
  assert.equal(calls,withCallback?2:0);
 }
 console.log('PASS first answer chunk collapses fresh/resumed trails once; user may reopen during remaining stream');
})().catch(e=>{console.error(e);process.exitCode=1;});
