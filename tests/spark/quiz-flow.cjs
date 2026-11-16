const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class E {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.disabled=false;this.hidden=false;this.attrs={};this.textContent='';}
 append(...ns){this.children.push(...ns);} appendChild(n){this.append(n);return n;}
 replaceChildren(...ns){this.children=ns;this.textContent='';}
 setAttribute(k,v){this.attrs[k]=v;} addEventListener(k,f){this['on'+k]=f;}
 click(){if(!this.disabled)return this.onclick?.();}
 querySelectorAll(sel){return this.children.flatMap(c=>[...(sel.split(',').some(x=>x.trim().startsWith('[')?c.dataset.quizNavigation:x.trim()===c.tagName)?[c]:[]),...c.querySelectorAll(sel)]);}
}
const source=fs.readFileSync('static/js/spark-study-sessions.js','utf8');
const start=source.indexOf('    function quizMetricsText'),end=source.indexOf('    function updateSessionProgress',start);
const ctx={document:{createElement:t=>new E(t)},el:(t,c,text)=>{const n=new E(t);n.className=c;n.textContent=text||'';return n;}};
vm.createContext(ctx);vm.runInContext(source.slice(start,end)+';this.renderQuestion=renderQuestion;this.renderQuizPanel=renderQuizPanel;',ctx);
const q=(id,extra={})=>({qid:id,prompt:'Question '+id,options:['a','b','c','d'],...extra});
const metrics={completion_percent:50,answered:1,total:2,accuracy_percent:0,correct:0,graded:1};
const assessment=questions=>({id:'a',questions,metrics,title:'Quiz',counts_toward_mastery:false});
const buttons=box=>box.querySelectorAll('button');
const send=box=>buttons(box).find(n=>n.textContent==='Send'||n.textContent==='Sending…');
(async()=>{
 let calls=0,resolve;
 ctx.api=()=>{calls++;return new Promise(r=>resolve=r);};
 let a=assessment([q('1'),q('2')]);let box=ctx.renderQuestion({id:'s'},a,a.questions[0]);
 buttons(box)[0].click();assert.equal(calls,0);const request=send(box).click();send(box).click();
 assert.equal(calls,1);assert.equal(send(box).disabled,true);
 resolve({result:{correct:false},assessment:assessment([q('1',{answered:true,submitted:true,correct:false,feedback:'Wrong'}),q('2')])});
 await request;assert.equal(send(box).disabled,true);assert.equal(send(box).hidden,true);
 assert.equal(box.children.find(n=>n.className==='spark-quiz-controls').hidden,true);
 assert.ok(box.children.some(n=>n.children.some(c=>c.textContent==='Incorrect')));
 // Reload restores wrong/submitted and inconclusive feedback without permitting a rewrite.
 for(const question of [q('1',{correct:false,submitted:true,feedback:'Wrong'}),q('1',{correct:null,submitted:true,outcome:'inconclusive',feedback:'Unverified'})]){
  box=ctx.renderQuestion({id:'s'},assessment([question]),question);assert.equal(send(box).disabled,true);
  assert.ok(box.children.some(n=>n.children.some(c=>c.textContent===question.feedback)));
 }
 a=assessment([q('1')]);ctx.api=async()=>{calls++;throw Error('Offline');};
 box=ctx.renderQuestion({id:'s'},a,a.questions[0]);buttons(box)[1].click();await send(box).click();
 assert.equal(a.questions[0]._quizDraft,'b');assert.equal(send(box).disabled,true);
 ctx.api=async()=>({assessments:[assessment([q('1')])]});
 await buttons(box).find(n=>n.textContent==='Check saved result').click();assert.equal(send(box).disabled,false);
 ctx.api=async()=>({result:{outcome:'invalid',feedback:'Fix answer'}});
 await send(box).click();assert.equal(send(box).disabled,false);assert.equal(send(box).hidden,false);assert.equal(a.questions[0]._quizDraft,'b');
 // Real panel function displays one question at a time and lets history reopen completed results.
 for(const count of [1,3]){
  a=assessment(Array.from({length:count},(_,i)=>q(String(i))));ctx.api=async()=>({assessments:[a]});
  const panel=new E('div');await ctx.renderQuizPanel(panel,{id:'s'});
  const questionNodes=()=>{const walk=n=>[...(n.className?.includes('spark-quiz-question')?[n]:[]),...n.children.flatMap(walk)];return walk(panel);};
  assert.equal(questionNodes().length,1);
  if(count===3){await buttons(panel).find(n=>n.textContent==='Next').click();assert.equal(questionNodes().length,1);assert.equal(questionNodes()[0].children[0].textContent,'Question 1');}
 }
 const css=fs.readFileSync('static/css/pwanimate/pwanimate.css','utf8');
 assert.match(css,/\.spark-quiz-question\{background:var\(--pwanimate-bg\)/);
 assert.match(css,/--pwanimate-bg: #f8fafc/);assert.match(css,/--pwanimate-bg: #18191f/);
 console.log('PASS quiz DOM: explicit Send, immediate lock, single/multiple sequence, reload outcomes, failed request reconciliation, invalid retry, solid light/dark tokens');
})().catch(e=>{console.error(e);process.exitCode=1;});
