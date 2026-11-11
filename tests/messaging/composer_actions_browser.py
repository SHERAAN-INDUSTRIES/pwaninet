"""Run the real composer controllers together; replace network/recorder services only."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
UI = '/static/js/chat/ui/ui-controller.js'
VOICE = '/static/js/chat/features/voice/voice-modal-controller.js'
MOCK = '''
export const EVENTS={};
export const eventBus={on(){},emit(){}};
export const store={};
export class MessageRenderer {}
export const contextMenuService={};
export const escapeHtml=value=>value, getCSRFToken=()=>'';
export const messageSoundManager={unlock:async()=>{}};
export const messageService={sendMessage:async text=>window.calls.sent.push(text),sendTypingIndicator(){}};
export const voiceService={isRecording:false,startRecording(){window.calls.started++;this.isRecording=true;},stopRecording(){window.calls.stopped++;this.isRecording=false;}};
'''
HTML = '''<textarea id="messageInput"></textarea><button id="voiceBtn" type="button"><span id="micIconWrapper">Mic</span><span id="sendIconWrapper">Send</span></button>
<script type="module">
window.calls={sent:[],started:0,stopped:0};
const {UIController}=await import('/static/js/chat/ui/ui-controller.js');
const {VoiceModalController}=await import('/static/js/chat/features/voice/voice-modal-controller.js');
window.ui=new UIController();window.voice=new VoiceModalController();
voice._initializeElements();
// Register voice before UI too: the send action must not depend on listener order.
voice._setupButtonListeners();voice._setupVoiceGestures();ui._setupUIEventHandlers();
window.ready=true;
</script>'''

def main():
    results=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
        for touch in [False,True]:
            context=browser.new_context(has_touch=touch,is_mobile=touch)
            errors=[]
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            def route(request):
                path=urlsplit(request.request.url).path
                if path=='/':return request.fulfill(body=HTML,content_type='text/html')
                body=(ROOT/path.lstrip('/')).read_text() if path in [UI,VOICE] else MOCK
                request.fulfill(body=body,content_type='application/javascript')
            page.route('https://composer.test/**',route)
            page.goto('https://composer.test/');page.wait_for_function('()=>window.ready===true')
            field=page.locator('#messageInput');button=page.locator('#voiceBtn')
            field.fill('Hello')
            if touch:button.tap()
            else:button.click()
            page.wait_for_timeout(300)
            assert page.evaluate('window.calls')=={'sent':['Hello'],'started':0,'stopped':0}
            assert field.input_value()==''
            # A separate, intentional mic tap must still start once (never start then stop).
            if touch:button.tap()
            else:button.click()
            page.wait_for_timeout(300)
            assert page.evaluate('window.calls')=={'sent':['Hello'],'started':1,'stopped':0}
            assert not errors,errors
            results.append(('Touch' if touch else 'Mouse')+': send once without recording; next mic tap starts once')
            context.close()
        browser.close()
    print(json.dumps({'passed':results},indent=2))

if __name__=='__main__':main()
