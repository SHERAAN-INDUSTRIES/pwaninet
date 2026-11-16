/* Loaded only by the disposable browser regression harness. */
const failures=[];let checks=0;
const check=(ok,label)=>{checks++;if(!ok)failures.push(label);};
try {
    const chat=new TestChat();chat.sanitizeToolCallTokens=x=>x;
    const render=text=>{const el=document.createElement('div');el.innerHTML=chat.renderMarkdownWithMathAndCode(text);return el;};
    const matrix=String.raw`Compute $$\begin{pmatrix}1&2\\3&4\end{pmatrix}$$.`;
    let el=render(matrix);
    check(Boolean(el.querySelector('.katex-display .mtable')),'matrix renders with rows');
    check(el.querySelector('annotation')?.textContent.includes(String.raw`2\\3`),'matrix double backslash preserved');
    for(const value of [String.raw`$x_1^2+\frac{1}{2}$`,String.raw`\(x_1^2+\frac{1}{2}\)`,String.raw`$$x^2=4$$`,String.raw`\[x^2=4\]`])check(Boolean(render(value).querySelector('.katex')),'math delimiter '+value);
    el=render('**Question**\n\n- First\n- Second\n\n```tex\n$x^2$\n```\n\nInline `$x_1$`.');
    check(Boolean(el.querySelector('strong')) && el.querySelectorAll('li').length===2,'Markdown emphasis/list');
    check(el.querySelector('pre code')?.textContent.includes('$x^2$'),'fenced code stays literal');
    check(el.querySelectorAll('.katex').length===0,'code not parsed as math');
    el=render(String.raw`$\notACommand{broken$`);check(Boolean(el.querySelector('.katex-error')),'malformed TeX safe fallback');
    window.pwned=false;
    el=render('<img src="x" onerror="window.pwned=true"><script>window.pwned=true</script>[bad](javascript:alert(1))'+String.raw`$\href{javascript:alert(1)}{bad}$`);
    document.body.append(el);
    check(!el.querySelector('script,[onerror],[onclick],a[href^="javascript:"]') && !window.pwned,'XSS removed');
    const purifier=window.DOMPurify;window.DOMPurify=null;
    el=render('<img src=x onerror=alert(1)>');check(!el.querySelector('img') && el.textContent.includes('<img'),'missing sanitizer fails closed');window.DOMPurify=purifier;
    chat.resumeInteraction=async()=>{};chat.isGenerating=false;
    for(const theme of ['light','dark'])for(const width of [390,1280]){
        const bg=theme==='dark'?'#171a1d':'#f8f9fa';
        const composerBg=theme==='dark'?'#242526':'#ffffff';
        document.documentElement.style.setProperty('--pwanimate-bg',bg);
        document.documentElement.style.setProperty('--pwanimate-composer-bg',composerBg);
        const host=document.createElement('div');host.style.width=width+'px';document.body.append(host);
        const pi={run_id:'run',interaction_id:'matrix-'+theme+width,prompt:matrix,input_type:'text'};
        const block=chat._buildInteractionBlock(pi);host.append(block);
        check(Boolean(block.querySelector('.spark-interaction-prompt .katex')),'active/restored question uses shared math renderer');
        // Typed questions merge into the composer surface: the panel reuses
        // the composer background token, matching the unified design.
        const expected=theme==='dark'?'rgb(36, 37, 38)':'rgb(255, 255, 255)';
        check(getComputedStyle(block).backgroundColor===expected,'opaque '+theme);
        check(block.scrollWidth<=host.clientWidth,'card fits '+width);
        host.remove();
    }
    const options=chat._questionControls({input_type:'single_choice',options:[{id:'a',label:String.raw`$\frac12$`}],allow_custom_answer:false},null,()=>{});
    const optionHost=document.createElement('div');optionHost.append(options.el);check(Boolean(optionHost.querySelector('.katex')),'option labels use shared math renderer');
    window._activeSparkChat=chat;
    const trail=window.SparkActivity.create(false);trail.add({id:'verify',kind:'verify',text:'untrusted',status:'completed'});
    check(Boolean(trail.element.querySelector('.spark-activity-text')),'completed activity uses shared renderer');
} catch(e) {failures.push(e.stack||String(e));}
document.body.dataset.result=failures.length?'FAIL':'PASS';
const result=document.createElement('pre');result.id='test-result';result.textContent=JSON.stringify({checks,failures});document.body.append(result);
