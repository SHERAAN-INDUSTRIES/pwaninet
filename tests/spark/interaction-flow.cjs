// Exercise the actual interaction builder with a small DOM adapter; no server/provider.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/pwanimate/pwanimate-chat.js', 'utf8');
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.disabled=false;
    this.classList={add(){},remove(){}}; }
  append(...nodes){ this.children.push(...nodes); }
  appendChild(n){this.append(n);return n;}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  addEventListener(name, fn){this['on'+name]=fn;}
  remove(){this.removed=true;}
  after(n){this.children.push(n);}
  focus(){}
  querySelector(sel){return this.querySelectorAll(sel)[0] || null;}
  querySelectorAll(sel){let found=[]; for(const c of this.children){
    if(sel.split(',').some(x=>x.trim().startsWith('.') ? c.className?.split(' ').includes(x.trim().slice(1)) : c.tagName===x.trim()))found.push(c);
    found.push(...c.querySelectorAll(sel));} return found;}
}
const values = new Map();
const context = {document:{createElement:tag=>new Element(tag)},window:{},
  localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}};
vm.createContext(context);
const begin=source.indexOf('        _buildInteractionBlock(pi) {');
const end=source.indexOf('        // Mark the compact trail',begin);
const resumeBegin=source.indexOf('        async _resumeChatPayload(');
const resumeEnd=source.indexOf('        // ------------------------------------------------- proposed actions',resumeBegin);
context.AbortController=AbortController;
context.getCsrfToken=()=>'';
const Builder=vm.runInContext('(class {'+source.slice(begin,end)+source.slice(resumeBegin,resumeEnd)+'})',context);
function make(questions){
 const ui=new Builder();ui.renderMarkdownWithMathAndCode=text=>'rendered:'+String(text); ui.isGenerating=false; ui.calls=[];
 ui._questionControls=(q,initial,change)=>{
   let current=initial;ui.visible=q.id;ui.answer=value=>{current=value;change(value);};
   return {el:new Element('input'),controls:[],read:()=>current};};
 ui.resumeInteraction=async(...args)=>{ui.calls.push(args);if(ui.reject)throw Error('Try again');};
 const block=ui._buildInteractionBlock({input_type:questions.length>1?'group':'text',
   questions,interaction_id:Math.random().toString(),run_id:'run',prompt:questions[0].prompt || 'Single'});
 const send=()=>block.querySelectorAll('button').find(n=>n.textContent==='Send').onclick();
 return {ui,block,send};
}
(async()=>{
 const {ui,block,send}=make([{id:'a',prompt:'A',input_type:'single_choice'},
  {id:'b',prompt:'B',input_type:'text'}, {id:'c',prompt:'C',input_type:'confirm'}]);
 send();assert.equal(ui.visible,'a');assert.equal(ui.calls.length,0);
 ui.answer({selected:['first']});assert.equal(ui.calls.length,0);send();assert.equal(ui.visible,'b');
 send();assert.equal(ui.visible,'b'); // missing required answer cannot advance
 ui.answer({text:'second'});send();assert.equal(ui.visible,'c');assert.equal(ui.calls.length,0);
 block.querySelectorAll('button').find(n=>n.textContent==='Back').onclick();
 assert.equal(ui.visible,'b');send();assert.equal(ui.visible,'c');
 ui.answer({confirmed:false});ui.reject=true;send();send();
 assert.equal(ui.calls.length,1);await new Promise(setImmediate);
 assert.match(block.querySelector('.spark-interaction-status').textContent,/Try again/);
 ui.reject=false;send();send();await new Promise(setImmediate);assert.equal(ui.calls.length,2);
 assert.equal(JSON.stringify(ui.calls[1][2]),JSON.stringify({answers:{a:{selected:['first']},b:{text:'second'},c:{confirmed:false}}}));
 const restoredMath=make([{id:'math',prompt:'Compute $x^2$'}]);
 assert.equal(restoredMath.block.querySelector('.spark-interaction-prompt').innerHTML,'rendered:Compute $x^2$');
 // Typed questions merge into the composer: no in-panel Send; the composer
 // submits the atomic answer through the same durable resume contract.
 const single=make([{id:'one'}]);
 assert.equal(typeof single.block._composerAnswer,'function');
 assert.equal(JSON.stringify(single.block._composerAnswer('only')),JSON.stringify({text:'only'}));
 assert.equal(single.block._composerAnswer(''),null);
 assert.equal(single.block.querySelectorAll('button').find(n=>n.textContent==='Send'),undefined);
 const continuation=new Builder(); continuation.pendingInteraction={interaction_id:'pending'};
 continuation.setGenerating=v=>{continuation.isGenerating=v;};
 continuation.setWaitingForInput=v=>{continuation.waitingForInput=v;};
 for(const name of ['showTypingIndicator','removeTypingIndicator','showSystemNotice'])continuation[name]=()=>{};
 context.fetch=async()=>({headers:{get:()=> 'application/json'},status:400,json:async()=>({error:'Rejected'})});
 await assert.rejects(continuation._resumeChatPayload({resume_run_id:'run'},null),/Rejected/);
 assert.equal(continuation.waitingForInput,true);assert.equal(continuation.isGenerating,false);
 // Shared SSE continuation contract still resolves/removes the active interaction.
 context.fetch=async()=>({headers:{get:()=> 'text/event-stream'},status:200});
 continuation.readChatEventStream=async()=>({status_code:200,data:{answer:'Done',activity:[]}});
 continuation._clearRunState=()=>{continuation.pendingInteraction=null;};
 continuation.appendAssistantMessage=()=>({contains:()=>false});
 for(const name of ['adoptActivity','scrollToMessageStart','refreshConversationsList'])continuation[name]=()=>{};
 await continuation._resumeChatPayload({resume_run_id:'run'},null);
 assert.equal(continuation.pendingInteraction,null);assert.equal(continuation.waitingForInput,false);
 const css=fs.readFileSync('static/css/pwanimate/pwanimate.css','utf8');
 assert.match(css,/\.spark-interaction-block\{[^}]*background:var\(--pwanimate-bg\)/);
 assert.doesNotMatch(css,/\.spark-interaction-block\{[^}]*opacity:/);
 console.log('PASS: single/group explicit Send, merged composer answers, mixed payloads, required validation, Back/ID order, failed retry, rapid duplicate clicks, solid theme surface');
})().catch(e=>{console.error(e);process.exitCode=1;});
