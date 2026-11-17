const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const start=src.indexOf('        renderGeneratedDocuments('),end=src.indexOf('        async downloadGeneratedResource(',start);
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';}
 appendChild(el){el.parent=this;this.children.push(el);return el;}
 setAttribute(k,v){this.attrs[k]=v;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 querySelector(selector){return this.children.find(x=>selector==='.spark-generated-documents'&&x.className?.includes('spark-generated-documents'))||null;}
}
const context={URL,window:{location:{origin:'https://vasto.test'}},document:{createElement:tag=>new Element(tag)}};
const C=vm.runInNewContext('(class {'+src.slice(start,end)+'})',context),chat=new C(),row=new Element('div');
const file={format:'docx',title:'<script>not HTML</script>',download_url:'/documents/document/12345678-abcd-1234-abcd-123456789abc/download/?t=signed&file_id=1'};
chat.renderGeneratedDocuments(row,[file,{...file,format:'pptx'},{...file,download_url:'https://evil.test/leak'},{...file,download_url:'javascript:alert(1)'},{...file,format:'zip'}],[{format:'doc'}]);
let box=row.children[0];assert.equal(box.children.length,3);
assert.equal(box.children[0].textContent,'Download DOCX · <script>not HTML</script>');
assert.match(box.children[0].href,/^https:\/\/vasto.test\/documents\//);
assert.equal(box.children[2].dataset.format,'doc');
chat.renderGeneratedDocuments(row,[file],[]);assert.equal(row.children.length,1);assert.equal(row.children[0].children.length,1);
assert.ok(src.includes("['pdf', 'docx', 'doc', 'pptx'].includes(fileFormat)"));
const backend=fs.readFileSync('pwanimate/api/agent_runs.py','utf8');assert.ok(backend.includes("base['metadata']['generated_documents']"));
console.log('PASS actual file-card renderer: four-format contract, same-origin signed links, text-only titles, retry and replay deduplication');
