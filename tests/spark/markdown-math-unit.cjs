// Actual vendored KaTeX/Marked; sanitizer call boundary is mocked here.
// Full DOMPurify/XSS/layout checks live in markdown-math.cjs (real browser).
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const start=source.indexOf('        renderMarkdownWithMathAndCode('),end=source.indexOf('        getSafeExternalUrl(',start);
const katex=require('../../static/vendor/katex/katex.min.js'),marked=require('../../static/vendor/marked/marked.min.js');
const calls=[],sanitizeCalls=[];
const window={marked,katex:{renderToString:(code,options)=>{calls.push({code,options});return katex.renderToString(code,options);}},DOMPurify:{sanitize:(html,options)=>{sanitizeCalls.push({html,options});return html;}}};
const escapeHtml=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const C=vm.runInNewContext('(class {'+source.slice(start,end)+'})',{window,escapeHtml,console});
const chat=new C();chat.sanitizeToolCallTokens=x=>x;
const matrix=String.raw`\begin{pmatrix}1&2\\3&4\end{pmatrix}`;
let html=chat.renderMarkdownWithMathAndCode('$$'+matrix+'$$');
assert.equal(calls[0].code,matrix);assert.match(html,/mtable/);assert.equal(calls[0].options.trust,false);
for(const tex of [String.raw`$x_1^2+\frac12$`,String.raw`\(x_1^2\)`,String.raw`\[x^2=4\]`])assert.match(chat.renderMarkdownWithMathAndCode(tex),/katex/);
assert.equal(chat.renderMarkdownWithMathAndCode('Before `$x$` after').trim(),'<p>Before <code>$x$</code> after</p>');
let before=calls.length;
html=chat.renderMarkdownWithMathAndCode('**Question**\n\n- First\n- Second\n\n```tex\n$x^2$\n```\n\nInline `$x_1$`.');
assert.equal(calls.length,before);assert.match(html,/<strong>Question<\/strong>/);assert.match(html,/<li>First<\/li>/);assert.match(html,/<code class="language-tex">\$x\^2\$/);
assert.match(chat.renderMarkdownWithMathAndCode(String.raw`$\notACommand{broken$`),/katex-error/);
assert.match(chat.renderMarkdownWithMathAndCode(String.raw`$\href{javascript:alert(1)}{bad}$`),/katex/);
assert.ok(sanitizeCalls.at(-1).options.FORBID_TAGS.includes('script'));
assert.ok(sanitizeCalls.at(-1).html.includes('katex')); // final pass includes inserted math
const activitySource=fs.readFileSync('static/js/pwanimate/activity-trail.js','utf8');
const summaryStart=activitySource.indexOf('    const md ='),summaryEnd=activitySource.indexOf('    function conversationKey()',summaryStart);
const renderSummary=vm.runInNewContext('(function(){'+activitySource.slice(summaryStart,summaryEnd)+';return md;})()',{window:{_activeSparkChat:chat}});
const summary={};renderSummary(summary,'$x^2$');assert.match(summary.innerHTML,/katex/);
window.DOMPurify=null;
assert.equal(chat.renderMarkdownWithMathAndCode('<script>alert(1)</script>'),'&lt;script&gt;alert(1)&lt;/script&gt;');
window.DOMPurify={sanitize:()=> '[sanitized]'};
assert.equal(chat.renderMarkdownWithMathAndCode('<img onerror=alert(1)>'),'[sanitized]');
console.log('PASS real KaTeX/Marked: matrices/row separators, four delimiters, code/list Markdown, malformed TeX, untrusted-math options, required final sanitization/fail-closed boundary');
window.DOMPurify={sanitize:html=>html}; // restore the renderer boundary mock after the fail-closed checks
const teachingExamples=JSON.parse(fs.readFileSync('tests/spark/fixtures/teaching-examples.json','utf8'));
for(const [subject,markdown] of Object.entries(teachingExamples)) {
 const rendered=chat.renderMarkdownWithMathAndCode(markdown);
 assert.doesNotMatch(rendered,/katex-error/,subject);
 assert.ok(rendered.length>markdown.length/2,subject);
}
console.log('PASS authored WP2 mathematics, programming and theory examples render through the existing Markdown/KaTeX path');
