const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class E{
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.classes=new Set();this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),contains:c=>this.classes.has(c),toggle:(c,v)=>v?this.classes.add(c):this.classes.delete(c)};}
 append(...n){this.children.push(...n);}appendChild(n){this.append(n);return n;}setAttribute(){}focus(){}addEventListener(k,fn){this['on'+k]=fn;}
 querySelectorAll(sel){return this.children.flatMap(c=>[...(sel.startsWith('.')?c.className?.split(' ').includes(sel.slice(1)):c.tagName.toLowerCase()===sel)?[c]:[],...c.querySelectorAll(sel)]);}
}
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const start=source.indexOf('        _questionControls('),end=source.indexOf('        _buildInteractionBlock(',start);
const C=vm.runInNewContext('(class {'+source.slice(start,end)+'})',{document:{createElement:t=>new E(t),createDocumentFragment:()=>new E('fragment')},requestAnimationFrame:fn=>fn()});
const chat=new C();chat.renderMarkdownWithMathAndCode=s=>s;
let emissions=[];
const multi=chat._questionControls({input_type:'multi_choice',options:[{id:'a',label:'A'},{id:'b',label:'B'}],allow_custom_answer:false},null,v=>emissions.push(v));
multi.controls[0].onclick();assert.deepEqual(Array.from(multi.read().selected),['a']);
multi.controls[1].onclick();assert.deepEqual(Array.from(multi.read().selected),['a','b']);
multi.controls[0].onclick();assert.deepEqual(Array.from(multi.read().selected),['b']);
const restored=chat._questionControls({input_type:'single_choice',options:[{id:'a',label:'A'}],allow_custom_answer:false},{selected:['a']},()=>{});
assert.equal(restored.read().selected[0],'a');
const confirm=chat._questionControls({input_type:'confirm'},null,()=>{});
confirm.controls[1].onclick();assert.equal(confirm.read().confirmed,false);assert.equal(confirm.controls[1].classList.contains('is-selected'),true);
assert.equal(confirm.controls[0].classList.contains('is-selected'),false);
console.log('PASS actual question controls: restricted multiselect emits selections; restored selected IDs; explicit decline remains false and visibly selected');
