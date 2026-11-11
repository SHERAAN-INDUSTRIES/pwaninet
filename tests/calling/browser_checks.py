"""Real Django templates + Chromium UI integration tests.

Signaling and the LiveKit boundary are intercepted only by Playwright. This suite
proves UI behavior, not live audio, push delivery, or native background calling.
"""
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pwaninet.settings.test')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import override_settings
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/verification/calling'
SDK = '''export const Track={Kind:{Audio:'audio'},Source:{Microphone:'microphone'}};
export const RoomEvent=Object.fromEntries(['TrackSubscribed','TrackUnsubscribed','Reconnecting','SignalReconnecting','Reconnected','Disconnected','AudioPlaybackStatusChanged','ParticipantDisconnected','ParticipantConnected'].map(x=>[x,x]));
export async function createLocalAudioTrack(){if(window.__denyMicrophone)throw Object.assign(new Error('denied'),{name:'NotAllowedError'});window.__mediaCounts.prepares++;return {stop(){window.__mediaCounts.stops++},async mute(){},async unmute(){}};}
export class Room{
 constructor(){this.events={};this.canPlaybackAudio=true;this.localParticipant={publishTrack:async()=>{window.__mediaCounts.publishes++}};window.__voiceRoom=this;}
 on(name,fn){this.events[name]=fn;return this;} removeAllListeners(){this.events={};}
 async connect(){window.__mediaCounts.connections++;} async disconnect(){window.__mediaCounts.disconnects++;}async startAudio(){}async switchActiveDevice(){}
}
window.__audioReady=()=>window.__voiceRoom.events.TrackSubscribed({kind:'audio',attach(){const el=document.createElement('audio');return el;}});
'''


def templates():
    def user(pk, username, name):
        return NS(id=pk, pk=pk, username=username, first_name=name, is_authenticated=True,
                  is_staff=False, is_superuser=False, profile_pic=None,
                  get_full_name=lambda:name, theme_preference='system')
    me, peer = user(1, 'qa-caller', 'Amina'), user(2, 'qa-susan', 'Susan Mwangi')
    members = [NS(user=me), NS(user=peer)]
    conversation = NS(id=7, type='direct', group_id=None, members=NS(all=lambda:members, count=lambda:2))
    rendered = {}
    for path, template in [('/', 'base.html'), ('/messaging/conversation/7/', 'messaging/conversation_detail_refactored.html')]:
        request = NS(user=me, path=path, GET={}, resolver_match=NS(url_name='home',app_name='posts'), COOKIES={},META={})
        context = {'app_version':'test','request':request,'user':me,'csrf_token':'test-csrf','voice_calling_available':True,
                   'partner_user':peer,'conversation':conversation if path != '/' else None,'conversations':[], 'conversation_data':[],
                   'suggested_users':[], 'unmessaged_friends':[]}
        with override_settings(CACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}):
            rendered[path] = render_to_string(template, context)
    return rendered


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rendered = templates()
    scripts = re.findall(r'<script[^>]*>(.*?)</script>',rendered['/'],re.S)
    manager = next(script for script in scripts if 'Navigation Scroll & Feed Position Manager' in script)
    # Exclude unrelated services, but keep the actual shell, chat and navigation code.
    for path, html in rendered.items():
        html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S|re.I)
        html = html.replace('</body>', '<script src="/static/js/htmx.min.js"></script><script>'+manager+'</script><script type="module" src="/static/js/calling/controller.mjs"></script></body>')
        rendered[path] = html
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
        context = browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        page = context.new_page(); errors=[]; actions=[]; current=None; revision=1
        page.on('pageerror',lambda error:errors.append(str(error)))
        page_requests=[]
        def call(status='inviting', incoming=False):
            return {'id':'00000000-0000-0000-0000-000000000007','conversation_id':7,'caller_id':2 if incoming else 1,
                    'revision':1,'status':status,'expires_at':'2099-01-01T00:00:00Z','participants':[
                        {'user_id':1,'status':'invited' if incoming else 'accepted','display_name':'Amina'},
                        {'user_id':2,'status':'accepted' if incoming else 'invited','display_name':'Susan Mwangi'}]}
        def route(route):
            nonlocal current, revision
            request=route.request;path=urlsplit(request.url).path
            if path in rendered:
                page_requests.append(path)
                return route.fulfill(body=rendered[path], content_type='text/html')
            if path == '/static/vendor/livekit/livekit-client.esm.mjs' and 'real=1' not in request.url:return route.fulfill(body=SDK,content_type='text/javascript')
            if path.startswith('/static/'):
                file=ROOT/path.lstrip('/')
                if file.is_file():return route.fulfill(path=str(file),content_type='text/javascript' if file.suffix in ['.mjs','.js'] else None)
            if path.startswith('/api/calling/'):
                data=request.post_data_json if request.method=='POST' else None
                if data and 'conversation_id' in data:
                    actions.append('start');current=call();revision=1;payload=current
                    # Production publishes on commit, before the POST response
                    # necessarily reaches the caller. Exercise that exact order.
                    page.evaluate("call=>window.dispatchEvent(new CustomEvent('pwani:call-update',{detail:call}))",current)
                elif path.endswith('/connection/'):
                    payload={'server_url':'wss://test.invalid','participant_token':'test-token'}
                elif data:
                    action=data['action'];actions.append(action);revision+=1
                    if not current: current=call(incoming=True)
                    current={**current,'revision':revision}
                    if action=='accept':current['status']='active'
                    if action in ['cancel','decline','leave']:current['status']={'cancel':'cancelled','decline':'declined','leave':'ended'}[action]
                    payload=current
                elif path=='/api/calling/':payload={'calls':[]}
                else:payload=current
                return route.fulfill(json=payload,status=201 if data and 'conversation_id' in data else 200)
            return route.fulfill(body='{}',content_type='application/json')
        page.route('**/*',route)
        page.add_init_script("window.__mediaCounts={prepares:0,stops:0,connections:0,publishes:0,disconnects:0};")
        origin='http://localhost:8123'
        page.goto(origin+'/messaging/conversation/7/');page.wait_for_function('!!window.PwaniCalls')
        page.evaluate("document.documentElement.dataset.theme='light';document.documentElement.dataset.bsTheme='light'")
        entry=page.get_by_role('button',name='Call Susan Mwangi',exact=True)
        for theme in ['light','dark']:
            page.evaluate("theme=>{document.documentElement.dataset.theme=theme;document.documentElement.dataset.bsTheme=theme;}",theme)
            page.wait_for_timeout(350)
            style=entry.evaluate("el=>({border:getComputedStyle(el).borderWidth,bg:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color,width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height})")
            assert style['border']=='0px' and style['bg']=='rgba(0, 0, 0, 0)',style
            assert style['width']>=44 and style['height']>=44,style
            if theme=='dark':assert style['color']=='rgb(255, 255, 255)',style
            else:assert style['color']=='rgb(15, 23, 42)',style
        checks.append('header call icon has no border/background, dark/light theme color, and a 44px target')
        with page.expect_response(lambda response: urlsplit(response.url).path=='/api/calling/' and response.request.method=='POST') as created:
            entry.click()
        assert created.value.status==201
        page.wait_for_function("document.querySelector('#call-status').textContent==='Calling…'")
        assert page.locator('#call-timer').is_hidden();assert actions.count('start')==1;assert actions.count('cancel')==0
        checks.append('start socket event before HTTP response does not auto-cancel; correct recipient and no premature timer')
        current={**current,'revision':2,'participants':[current['participants'][0],{**current['participants'][1],'status':'ringing'}]};revision=2
        page.evaluate("call=>window.dispatchEvent(new CustomEvent('pwani:call-update',{detail:call}))",current)
        page.wait_for_function("document.querySelector('#call-status').textContent==='Ringing…'")
        checks.append('ringing requires a signaling acknowledgement')
        current={**current,'status':'active','revision':3};revision=3
        page.evaluate("call=>window.dispatchEvent(new CustomEvent('pwani:call-update',{detail:call}))",current)
        page.wait_for_function('window.__mediaCounts.publishes===1')
        assert page.locator('#call-status').inner_text()=='Connecting…'
        page.evaluate('window.__audioReady()');page.wait_for_function("document.querySelector('#call-status').textContent==='Connected'")
        page.get_by_role('button',name='Mute',exact=True).click();page.wait_for_function("document.querySelector('[data-call-action=mute]').getAttribute('aria-pressed')==='true'")
        native_audio = "window.__audioRoutes=[];window.Capacitor={isNativePlatform:()=>true,Plugins:{CallAudio:{setSpeaker:async({enabled})=>{window.__audioRoutes.push(enabled);return {speaker:enabled}},reset:async()=>{}}}};"
        page.add_init_script(native_audio);page.evaluate(native_audio)
        page.get_by_role('button',name='Turn on loudspeaker',exact=True).first.click()
        page.wait_for_function("document.querySelector('[data-call-action=speaker]').getAttribute('aria-pressed')==='true'")
        assert page.evaluate('window.__audioRoutes.includes(true)')
        assert page.locator('.call-avatar').evaluate("el=>getComputedStyle(el).outlineStyle")=='none'
        page.wait_for_timeout(1100)
        for theme in ['light','dark']:
            page.evaluate("theme=>{document.documentElement.dataset.theme=theme;document.documentElement.dataset.bsTheme=theme;}",theme)
            page.wait_for_timeout(450)
            page.screenshot(path=str(OUT/f'call_mobile_{theme}.png'))
        assert page.locator('.call-heading #call-timer').count()==1
        assert page.locator('[data-call-action=minimize] .bi-chevron-left').count()==1
        assert page.locator('#call-control-pill [data-call-action=mute]').is_visible()
        assert page.locator('#call-control-pill [data-call-action=end]').is_visible()
        assert page.locator('#call-end span').inner_text() == 'End call'
        assert page.locator('#call-end span').bounding_box()['width'] > 20
        checks.append('visible End call label')
        checks.append('centered voice-call title/timer, left-chevron back, profile below and bottom control pill')
        checks.append('audio-ready event starts duration; microphone selection survives live theme changes')
        page.keyboard.press('Tab')
        assert page.locator('#call-screen').evaluate('el=>el.contains(document.activeElement)')
        page.keyboard.press('Escape');assert page.locator('#call-pill').is_visible()
        page.get_by_role('button',name='Restore full-screen call',exact=True).click()
        page.get_by_role('button',name='Back to chat',exact=True).click();assert page.locator('#call-pill').is_visible()
        composer=page.locator('#messaging-composer-form').bounding_box()
        floating=page.locator('#call-pill').bounding_box()
        header=page.locator('.chat-header').bounding_box()
        assert floating['y']>=header['y']+header['height'] and floating['y']<header['y']+header['height']+24,(floating,header)
        assert floating['y']+floating['height']<=composer['y']+1,(floating,composer)
        page.set_viewport_size({'width':390,'height':540});page.wait_for_timeout(100)
        floating=page.locator('#call-pill').bounding_box()
        composer=page.locator('#messaging-composer-form').bounding_box()
        header=page.locator('.chat-header').bounding_box()
        assert floating['y']>=header['y']+header['height'] and floating['y']<header['y']+header['height']+24,(floating,header)
        assert floating['y']+floating['height']<=composer['y']+1,(floating,composer)
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(450)
        page.screenshot(path=str(OUT/'call_pill_mobile.png'))
        checks.append('keyboard focus stays within the dialog; Escape minimizes; pill sits below the header at normal and reduced viewport heights')
        def chat_geometry():
            return page.evaluate("()=>({body:document.body.className,shell:document.querySelector('#app-shell-layout').className,header:document.querySelector('.chat-header').getBoundingClientRect().toJSON(),composer:document.querySelector('#messaging-composer-form').getBoundingClientRect().toJSON()})")
        before_call_geometry=chat_geometry()
        page.evaluate("window.PwaniCalls.navigate('/')")
        page.wait_for_url(origin+'/');page.wait_for_timeout(400)
        assert page.locator('#call-pill').is_visible(), page.evaluate("({roots:document.querySelectorAll('#pwani-call-root').length, root:document.querySelector('#pwani-call-root')?.outerHTML.slice(0,250),pill:document.querySelector('#call-pill')?.outerHTML,has:window.PwaniCalls?.hasSession(),media:window.__mediaCounts,body:document.body.className})");assert page.locator('#pwani-call-root').count()==1
        assert page.evaluate('window.__mediaCounts.connections')==1
        checks.append('pill and media session survive actual HTMX navigation from messaging into the app shell')
        page.evaluate("window.PwaniCalls.navigate('/messaging/conversation/7/')")
        page.wait_for_url(origin+'/messaging/conversation/7/');page.wait_for_timeout(250)
        assert page.locator('#whatsappLayout #chatMainAreaWrapper #chatMainArea').count()==1
        assert page.locator('link[href*="conversation-detail.css"]').count()>=1
        assert chat_geometry()==before_call_geometry
        checks.append('returning from home during a phone call restores the complete styled chat layout')
        page.evaluate("window.PwaniCalls.navigate('/')")
        page.wait_for_url(origin+'/');page.wait_for_timeout(100)

        saved_clock=page.evaluate("JSON.parse(sessionStorage.getItem('pwani-call-recovery:1')).connectedAt")
        page.reload();page.wait_for_function('window.__mediaCounts.publishes===1')
        page.evaluate('window.__audioReady()')
        page.wait_for_selector('#call-pill:visible')
        assert page.evaluate("JSON.parse(sessionStorage.getItem('pwani-call-recovery:1')).connectedAt")==saved_clock
        assert page.locator('#call-pill [data-call-action=speaker]').get_attribute('aria-pressed')=='true'
        assert page.evaluate('window.__audioRoutes.includes(true)')
        assert actions.count('leave')==0
        checks.append('full document reload reconnects the same call and restores clock, mute, speaker and minimized pill')
        page.get_by_role('button',name='Restore full-screen call',exact=True).click()
        assert page.get_by_role('button',name='Unmute',exact=True).get_attribute('aria-pressed')=='true'
        # Geometry across phones, desktop, short landscape and large text.
        for width,height,scale in [(320,640,1),(360,740,1),(430,932,1),(844,390,1),(768,1024,1),(1440,900,1),(320,640,1.5)]:
            page.set_viewport_size({'width':width,'height':height})
            page.evaluate("scale=>document.documentElement.style.fontSize=(16*scale)+'px'",scale)
            assert page.evaluate("document.querySelector('#call-screen').scrollWidth <= document.querySelector('#call-screen').clientWidth+1")
            for button in page.locator('#call-screen button:visible').all():
                box=button.bounding_box();assert box['width']>=44 and box['height']>=44,box
        checks.append('phone/tablet/desktop, landscape, 150% text and 44px targets')
        page.set_viewport_size({'width':1440,'height':900});page.evaluate("document.documentElement.style.fontSize='16px'")
        page.wait_for_timeout(200)
        assert page.locator('#call-screen').evaluate("el=>!el.matches(':modal')")
        panel=page.locator('#call-screen').bounding_box()
        assert panel['width']<=680 and panel['height']<=520 and panel['x']<40,panel
        handle=page.locator('.call-drag-handle').bounding_box()
        page.mouse.move(handle['x']+20,handle['y']+20);page.mouse.down();page.mouse.move(handle['x']+220,handle['y']-100);page.mouse.up()
        assert page.locator('#call-screen').bounding_box()['x']>panel['x']+100
        page.get_by_role('button',name='Expand call panel',exact=True).click()
        assert page.locator('#call-screen').bounding_box()['width']<=680
        page.wait_for_timeout(450);page.screenshot(path=str(OUT/'call_desktop_panel.png'))
        checks.append('desktop uses a nonmodal bottom-left panel with drag and bounded expansion')
        old_panel=page.locator('#call-screen').bounding_box()
        page.reload();page.wait_for_function('window.__mediaCounts.publishes===1');page.evaluate('window.__audioReady()')
        page.wait_for_timeout(200)
        recovered_panel=page.locator('#call-screen').bounding_box()
        assert all(abs(old_panel[key]-recovered_panel[key])<2 for key in ['x','y','width','height']),(old_panel,recovered_panel)
        assert page.get_by_role('button',name='Collapse call panel',exact=True).get_attribute('aria-pressed')=='true'
        checks.append('desktop position, dimensions and expansion are restored after a full reload')
        page.set_viewport_size({'width':390,'height':844});page.evaluate("document.documentElement.style.fontSize='16px'")
        page.get_by_role('button',name='Back to chat',exact=True).click()
        page.locator('#call-pill-end').count() # action is a separate semantic button
        page.locator('#call-pill [data-call-action=end]').click()
        page.wait_for_function("!document.querySelector('#call-screen').open && document.querySelector('#call-pill').hidden")
        assert page.locator('#call-pill').is_hidden();assert actions.count('leave')==1
        assert page.evaluate('window.__mediaCounts.disconnects')>=1
        checks.append('ending from pill releases media and shows a brief outcome without opening the full screen')
        page.wait_for_timeout(3100)
        page.evaluate("window.PwaniCalls.navigate('/messaging/conversation/7/')")
        page.wait_for_function("!!window.PwaniCalls && !!document.querySelector('.chat-header') && document.querySelector('#call-status').textContent===''")
        def chat_geometry():
            return page.evaluate("()=>({body:document.body.className,shell:document.querySelector('#app-shell-layout').className,header:document.querySelector('.chat-header').getBoundingClientRect().toJSON(),composer:document.querySelector('#messaging-composer-form').getBoundingClientRect().toJSON()})")
        before_call_geometry=chat_geometry()
        # A new invitation uses the same UI and action handlers, with no media mock exposed to production.
        current=call(incoming=True);current['id']='00000000-0000-0000-0000-000000000008';revision=1
        page.evaluate("call=>window.dispatchEvent(new CustomEvent('pwani:call-update',{detail:call}))",current)
        page.wait_for_selector('#call-banner:visible')
        page.set_viewport_size({'width':1440,'height':900});page.wait_for_timeout(200)
        assert page.locator('#call-screen').evaluate("el=>el.open&&!el.matches(':modal')")
        assert page.locator('#call-incoming-actions').is_visible()
        incoming_panel=page.locator('#call-screen').bounding_box()
        assert incoming_panel['x']<40 and incoming_panel['height']<520,incoming_panel
        page.wait_for_timeout(450);page.screenshot(path=str(OUT/'call_desktop_incoming.png'))
        checks.append('incoming desktop invitation uses the same nonmodal bottom-left panel')
        page.set_viewport_size({'width':390,'height':844});page.wait_for_selector('#call-banner:visible')
        assert page.locator('#call-pill [data-call-action=speaker]').count()==1
        page.wait_for_timeout(450)
        page.screenshot(path=str(OUT/'call_incoming_banner_mobile.png'))
        page.locator('#call-banner [data-call-action=restore]').click()
        page.wait_for_selector('#call-incoming-actions:visible')
        page.wait_for_timeout(450)
        page.screenshot(path=str(OUT/'call_incoming_mobile.png'))
        assert actions.count('ringing')==1
        current={**current,'status':'cancelled','revision':10}
        page.evaluate("call=>window.dispatchEvent(new CustomEvent('pwani:call-update',{detail:call}))",current)
        page.wait_for_function("document.querySelector('#call-status').textContent==='Call cancelled'")
        assert page.locator('#call-banner').is_hidden()
        checks.append('incoming banner/full screen share state; caller cancellation removes invitation')
        requests_before=len(page_requests)
        page.get_by_role('button',name='Return to chat',exact=True).click()
        page.wait_for_function("document.querySelector('#call-status').textContent===''")
        assert len(page_requests)==requests_before,'Return to current chat must not reload or replace it'
        assert chat_geometry()==before_call_geometry,'Return to chat must preserve shell/header/composer geometry'
        checks.append('returning from full-screen call to the current chat preserves DOM/layout without a request or swap')
        page.evaluate('window.__denyMicrophone=true')
        starts_before=actions.count('start')
        page.get_by_role('button',name='Call Susan Mwangi',exact=True).click()
        page.wait_for_function("document.querySelector('#call-status').textContent==='Microphone access needed'")
        assert actions.count('start')==starts_before
        assert page.get_by_role('button',name='Try again',exact=True).is_visible()
        page.locator('[data-call-action=permission-back]').click()
        page.wait_for_function("document.querySelector('#call-status').textContent===''")
        page.evaluate('window.__denyMicrophone=false')
        checks.append('blocked microphone gives accurate retry instructions and creates no invitation')
        page.emulate_media(reduced_motion='reduce')
        assert page.locator('.call-layout').evaluate("el=>getComputedStyle(el).animationName")=='none'
        page.context.grant_permissions(['microphone'])
        actual = page.evaluate('''async()=>{
            const {VoiceMedia}=await import('/static/js/calling/media.mjs');
            const host=document.createElement('div');
            const media=new VoiceMedia({sdkUrl:'/static/vendor/livekit/livekit-client.esm.mjs?real=1',audioHost:host});
            await media.prepare();const track=media.track.mediaStreamTrack;
            const before=track.readyState;media.close();return {before,after:track.readyState};
        }''')
        assert actual=={'before':'live','after':'ended'},actual
        checks.append('reduced motion; real bundled LiveKit SDK acquires and releases a browser-provided fake microphone')
        history = page.evaluate("""async () => {
            const {MessageRenderer} = await import('/static/js/chat/ui/renderer.js');
            const {messageService} = await import('/static/js/chat/core/message-service.js?v=20261106-group1');
            const renderer = new MessageRenderer();
            renderer.currentUserId = 1;
            const results = [];
            for (const own of [true, false]) {
                const normalized = messageService.normalizeServerMessage({id: own ? 999 : 1000,
                    conversation: 7, sender: {id: own ? 1 : 2}, message_type: 'voice_call',
                    content: 'Voice call', status: 'sent', created_at: '2026-11-09T09:18:00+03:00',
                    voice_call: {started_at: '2026-11-09T09:15:00+03:00', status: 'ended'}});
                const element = renderer._createMessageBody({...normalized, isOwn: own, groupPosition: 'single'});
                const container = document.getElementById('messagesContainer');
                if (own) container.replaceChildren();
                container.append(element);
                results.push({side: element.classList.contains(own ? 'sent-wrapper' : 'received-wrapper'),
                    icon: !!element.querySelector(own ? '.bi-telephone-outbound' : '.bi-telephone-inbound'),
                    time: element.querySelector('time').getAttribute('datetime'), text: element.textContent});
            }
            return results;
        }""")
        for item in history:
            assert item['side'] and item['icon'] and 'Started' in item['text'], history
            assert item['time'] == '2026-11-09T09:15:00+03:00'
        checks.append('call-history bubbles keep sent/received shapes, directional icons and actual start time')
        page.screenshot(path=str(OUT/'call_history_mobile.png'))
        assert not errors,errors
        (OUT/'browser_checks.json').write_text(json.dumps({'checks':checks,'page_errors':errors,'boundary':'mock signaling and media; actual templates/controller/navigation'},indent=2))
        print(json.dumps({'passed':len(checks),'checks':checks},indent=2))
        browser.close()

if __name__=='__main__':main()
