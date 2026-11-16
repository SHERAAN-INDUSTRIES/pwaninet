// Real vendored Marked/DOMPurify/KaTeX in a disposable headless Chrome profile.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process');
const root=process.cwd(),src=fs.readFileSync('static/js/pwanimate/pwanimate-chat.js','utf8');
const section=(start,end)=>src.slice(src.indexOf(start),src.indexOf(end,src.indexOf(start)));
const methods=section('        renderMarkdownWithMathAndCode(content)', '        getSafeExternalUrl(')+
 section('        _questionControls(q, initial, onChange, opts', '        // Mark the compact trail');
const escape=section('    function escapeHtml(str)', '    function isFinePointerDevice()');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'spark-math-browser-'));
const browser=process.env.SPARK_TEST_CHROME || '/usr/bin/google-chrome';
const fixtures=fs.readFileSync('tests/spark/fixtures/markdown-math.browser.js','utf8');
new (require('node:vm').Script)(fixtures); // Verify fixture syntax even when browser launch is blocked.
const html='<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="file://'+root+'/static/css/pwanimate/pwanimate.css"><link rel="stylesheet" href="file://'+root+'/static/vendor/katex/katex.min.css"></head><body>'+
 ['marked/marked.min.js','dompurify/purify.min.js','katex/katex.min.js'].map(f=>'<script src="file://'+root+'/static/vendor/'+f+'"></script>').join('')+
 '<script>'+escape+'class TestChat{'+methods+'}'+fs.readFileSync('static/js/pwanimate/activity-trail.js','utf8')+'</script><script src="file://'+root+'/tests/spark/fixtures/markdown-math.browser.js"></script></body></html>';
try{
 fs.writeFileSync(path.join(dir,'test.html'),html);
 const r=cp.spawnSync(browser,['--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--no-first-run','--user-data-dir='+path.join(dir,'profile'),'--allow-file-access-from-files','--virtual-time-budget=1000','--dump-dom','file://'+path.join(dir,'test.html')],{encoding:'utf8',timeout:20000,maxBuffer:4*1024*1024});
 const result=r.stdout?.match(/<pre id="test-result">([\s\S]*?)<\/pre>/)?.[1];
 if(r.status!==0 && !result && /Operation not permitted|Read-only file system/.test(r.stderr||'')){console.error('BLOCKED: browser process/socket permissions prevent real DOM/XSS/layout checks.');process.exitCode=2;}
 else if(r.status!==0 || !r.stdout?.includes('data-result="PASS"')){console.error('Browser tests failed:',result||r.error?.message||r.stderr?.slice(-1200));process.exitCode=1;}
 else console.log('PASS real-browser Markdown/math:',result);
}finally{fs.rmSync(dir,{recursive:true,force:true});}
