const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class E{
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.disabled=false;this.textContent='';}
 append(...ns){this.children.push(...ns);}appendChild(n){this.append(n);return n;}
 insertBefore(n,b){this.children.splice(this.children.indexOf(b),0,n);}
 setAttribute(){} focus(){}addEventListener(k,f){this['on'+k]=f;}
 remove(){this.removed=true;}replaceWith(n){this.replacement=n;}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 querySelectorAll(s){return this.children.flatMap(c=>[...(s.split(',').some(x=>x.trim().startsWith('.')?c.className?.split(' ').includes(x.trim().slice(1)):c.tagName===x.trim())?[c]:[]),...c.querySelectorAll(s)]);}
}
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const a=source.indexOf('        async refreshMemoryProposals()'),b=source.indexOf('        async refreshConversationsList()',a);
const ctx={document:{createElement:t=>new E(t)},getCsrfToken:()=>''};vm.createContext(ctx);
const C=vm.runInContext('(class {'+source.slice(a,b)+'})',ctx);
const button=(box,text)=>box.querySelectorAll('button').find(n=>n.textContent===text);
const fact=()=>({id:'fact',text:'Preferred response length: concise.',version:1,status:'proposed',conversation_id:'conversation'});
(async()=>{
 const chat=new C();let calls=0,done;
 ctx.fetch=async()=>{calls++;return new Promise(r=>done=r);};
 let box=chat._memoryApprovalBlock(fact());
 const pending=button(box,'Approve').onclick();button(box,'Approve').onclick();assert.equal(calls,1);
 done({ok:true,json:async()=>({fact:{status:'approved',version:2}})});await pending;assert.equal(box.removed,true);
 // Reject uses the existing consent API and removes only on success.
 ctx.fetch=async(url,options)=>{assert.equal(JSON.parse(options.body).action,'reject');return{ok:true,json:async()=>({})};};
 box=chat._memoryApprovalBlock(fact());await button(box,'Reject').onclick();assert.equal(box.removed,true);
 // Edit saves a fresh pending version; it does not submit approval implicitly.
 let actions=[];ctx.fetch=async(url,options)=>{const data=JSON.parse(options.body);actions.push(data);return{ok:true,json:async()=>({fact:{...fact(),text:data.text,version:2}})};};
 box=chat._memoryApprovalBlock(fact());button(box,'Edit').onclick();box.querySelector('textarea').value='Edited preference';
 await button(box,'Save edit').onclick();assert.equal(actions[0].action,'edit');assert.equal(actions.length,1);
 assert.equal(box.replacement.dataset.memoryId,'fact');
 // A stale version/consent response leaves the exact proposal reviewable with an error.
 ctx.fetch=async()=>({ok:false,json:async()=>({detail:'Consent changed'})});
 box=chat._memoryApprovalBlock(fact());await button(box,'Approve').onclick();assert.ok(!box.removed);assert.equal(button(box,'Approve').disabled,false);
 // Reload/resumed completion read persisted proposals and honor disable/current conversation.
 chat.transcript=new E('div');chat.conversationId='conversation';
 ctx.fetch=async()=>({ok:true,json:async()=>({enabled:true,facts:[fact(),{...fact(),id:'other',conversation_id:'other'}]})});
 await chat.refreshMemoryProposals();assert.equal(chat.transcript.children.length,1);
 ctx.fetch=async()=>({ok:true,json:async()=>({enabled:false,facts:[fact()]})});
 await chat.refreshMemoryProposals();assert.equal(chat.transcript.children[0].removed,true);
 // A model's ordinary acknowledgement creates no approval UI without a persisted fact.
 chat.transcript=new E('div');chat._memoryProposalNotice=null;
 ctx.fetch=async()=>({ok:true,json:async()=>({enabled:true,facts:[]})});
 await chat.refreshMemoryProposals();assert.equal(chat.transcript.children.length,0);
 // Both separately persisted preferences render after reload/resumption.
 ctx.fetch=async(url)=>{assert.ok(url.includes('conversation_id=conversation'));return{ok:true,json:async()=>({enabled:true,facts:[fact(),{...fact(),id:'format',text:'Preferred response format: bullet points.'}]})};};
 await chat.refreshMemoryProposals();assert.equal(chat.transcript.children.length,2);
 // A focus/replay refresh must not discard an unsaved edit or busy approval.
 const first=chat.transcript.children[0];button(first,'Edit').onclick();
 await chat.refreshMemoryProposals();assert.ok(!first.removed);
 // Memory off offers a settings route and creates no proposed fact.
 chat.transcript=new E('div');chat._memoryProposalNotice={status:'memory_disabled',conversationId:'conversation'};
 ctx.fetch=async()=>({ok:true,json:async()=>({enabled:false,facts:[]})});
 await chat.refreshMemoryProposals();assert.equal(chat.transcript.children.length,1);
 assert.equal(chat.transcript.querySelector('a').href,'/pwanimate/settings/memory/');
 assert.equal(chat.transcript.querySelectorAll('button').length,0);
 const css=fs.readFileSync('static/css/pwanimate/pwanimate.css','utf8');assert.match(css,/\.spark-memory-approval\{background:var\(--pwanimate-bg\)/);
 console.log('PASS memory approval DOM: approve/reject/edit, repeated clicks, stale error, reload conversation scope, disable, opaque theme token');
})().catch(e=>{console.error(e);process.exitCode=1;});
