"""Exercise the real viewer, gesture controller and CSS with Android-style insets."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
RENDERER='/static/js/chat/ui/renderer.js'
ZOOM='/static/js/chat/ui/media-zoom.js'
MOCK='''export const formatDateLabel=()=>'',formatTime=()=>'',formatPreciseTime=()=>'';
export const escapeHtml=value=>String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
export const renderMessageStatus=()=>'',linkPreviewRenderer={},EVENTS={},eventBus={},deviceMediaStore={};'''
HTML='''<style>:root{--vasto-safe-area-top:32px;--vasto-safe-area-bottom:28px;--vasto-safe-area-left:16px;--vasto-safe-area-right:20px}body{margin:0}</style>
<link rel="stylesheet" href="/static/css/chat/telegram-media.css">
<script type="module">
const {MessageRenderer}=await import('/static/js/chat/ui/renderer.js');
window.viewer=new MessageRenderer();
window.openViewer=()=>viewer.renderFullscreenMediaViewer([{type:'image',url:'/photo.svg',caption:'Photo one'},{type:'image',url:'/photo.svg'},{type:'video',url:'/clip.mp4'}]);
openViewer();window.ready=true;
</script>'''

def main():
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':390,'height':844},has_touch=True,is_mobile=True,device_scale_factor=1)
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def route(request):
            path=urlsplit(request.request.url).path
            if path=='/':return request.fulfill(body='<meta name="viewport" content="width=device-width, initial-scale=1">'+HTML,content_type='text/html')
            if path=='/photo.svg':return request.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><rect width="800" height="600" fill="#245ca6"/></svg>',content_type='image/svg+xml')
            if path=='/clip.mp4':return request.fulfill(status=204)
            real=path in [RENDERER,ZOOM] or path.endswith('.css')
            request.fulfill(body=(ROOT/path.lstrip('/')).read_text() if real else MOCK,content_type='text/css' if path.endswith('.css') else 'application/javascript')
        page.route('https://viewer.test/**',route)
        page.goto('https://viewer.test/');page.wait_for_function('()=>window.ready&&document.querySelector(".media-viewer-image").naturalWidth>0')
        page.wait_for_timeout(350)
        def safe():
            return page.evaluate('''()=>[...document.querySelectorAll('.media-viewer-header button,.media-viewer-footer,.media-viewer-nav,.media-viewer-floating-mute,.media-viewer-progress-container')].filter(node=>node.getBoundingClientRect().width).every(node=>{const r=node.getBoundingClientRect();return r.top>=32&&r.bottom<=innerHeight-28&&r.left>=16&&r.right<=innerWidth-20})''')
        assert safe()
        page.get_by_role('button',name='Zoom in',exact=True).click()
        assert page.get_by_role('button',name='Reset zoom').inner_text()=='150%'
        viewport=page.locator('.media-viewer-content').bounding_box();cx=viewport['x']+viewport['width']/2;cy=viewport['y']+viewport['height']/2
        page.mouse.move(cx,cy);page.mouse.down();page.mouse.move(cx+40,cy+30,steps=5);page.mouse.up()
        assert page.locator('.media-viewer-image').evaluate("node=>!node.style.transform.startsWith('translate(0px, 0px)')")
        page.get_by_role('button',name='Reset zoom').click()
        assert page.locator('.media-viewer-image').evaluate("node=>node.style.transform==='translate(0px, 0px) scale(1)'")
        checks.append('Zoom buttons, reset and bounded image dragging work')
        # Genuine two-finger touch events produce browser pointer events.
        cdp=context.new_cdp_session(page)
        for kind,distance in [('touchStart',40),('touchMove',80)]:
            cdp.send('Input.dispatchTouchEvent',{'type':kind,'touchPoints':[{'x':cx-distance,'y':cy,'id':1},{'x':cx+distance,'y':cy,'id':2}]})
        cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
        assert int(page.get_by_role('button',name='Reset zoom').inner_text().strip('%'))>=190
        checks.append('Two-finger pinch zoom works')
        page.get_by_role('button',name='Reset zoom').click()
        page.touchscreen.tap(cx,cy);page.touchscreen.tap(cx,cy)
        assert page.get_by_role('button',name='Reset zoom').inner_text()=='250%'
        checks.append('Touch double-tap zoom works without a duplicate synthetic click')
        page.get_by_role('button',name='Reset zoom').click()
        page.mouse.dblclick(cx,cy)
        assert page.get_by_role('button',name='Reset zoom').inner_text()=='250%'
        page.keyboard.press('0');assert page.get_by_role('button',name='Reset zoom').inner_text()=='100%'
        checks.append('Double-click and keyboard reset work')
        page.locator('.media-viewer-nav.next').click()
        assert page.get_by_role('button',name='Reset zoom').inner_text()=='100%'
        page.locator('.media-viewer-nav.next').click()
        assert page.locator('.media-viewer-zoom-controls').is_hidden()
        assert safe()
        checks.append('Changing attachments resets zoom; video controls stay inside safe areas')
        page.set_viewport_size({'width':844,'height':390});page.wait_for_timeout(200);assert safe()
        page.locator('.media-viewer-nav.prev').click();page.wait_for_timeout(200);assert safe()
        checks.append('Photo and video controls respect top, bottom and side insets in both orientations')
        page.screenshot(path='/tmp/pwaninet-media-viewer-landscape.png')
        page.get_by_role('button',name='Close media viewer').click();page.wait_for_timeout(350)
        assert page.locator('.media-viewer-overlay').count()==0
        assert page.evaluate('document.body.style.overflow')==''
        page.evaluate('openViewer();openViewer()');page.wait_for_timeout(350)
        assert page.locator('.media-viewer-overlay').count()==1
        page.keyboard.press('Escape');page.wait_for_timeout(350)
        assert page.locator('.media-viewer-overlay').count()==0
        assert page.evaluate('document.body.style.overflow')==''
        assert not errors,errors
        checks.append('Close/reopen cleans up gestures, keyboard handlers and page scroll lock')
        browser.close()
    print(json.dumps({'passed':checks,'page_errors':errors},indent=2))

if __name__=='__main__':main()
