"""Compare original Django component geometry with its saved DOM; no alternate UI."""
import json
import os
import re
import threading
import time
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','pwaninet.settings.local')
import django
django.setup()
from django.template import Template, Context
from django.template.loader import render_to_string
from playwright.sync_api import sync_playwright
from browser_checks import Fixture, ROOT

user=NS(id=7,pk=7,username='qa',first_name='QA',is_authenticated=True,is_staff=False,is_superuser=False,profile_pic=None,get_full_name=lambda:'QA',chat_theme_preference='ocean-wave')
conversation=NS(id=12,name='Study group',type='group',group=NS(id=3,name='Study group',get_photo_url='/static/images/default_group.jpg'),members=NS(count=lambda:2,all=lambda:[]))
context={'request':NS(user=user,path='/',resolver_match=NS(url_name='home')),'user':user,'conversation':conversation,'is_member':True}
base=(ROOT/'templates/base.html').read_text()
styles='\n'.join(re.findall(r'<style[^>]*>(.*?)</style>',base,re.S))
styles=Template('{% load static i18n %}'+styles).render(Context(context))
nav=render_to_string('partials/top_nav.html',context)
chat=render_to_string('messaging/partials/conversation_chat_content.html',context)
chat=re.sub(r'<script.*?</script>','',chat,flags=re.S)
# These are the actual component templates and actual application stylesheets.
css=['bootstrap.min.css','bootstrap-icons.css','custom.css','pwanimate/pwanimate.css','messaging/conversation-detail.css','messaging/chat-presets.css','messaging/media-composer.css','chat_theme.css','chat/telegram-media.css']
links=''.join(f'<link rel="stylesheet" href="/static/css/{name}">' for name in css)
scripts='<script src="/static/js/offline-resources.js" defer></script><script src="/static/js/offline-capture.js" defer></script><script src="/static/js/bootstrap.bundle.min.js"></script><script>navigator.serviceWorker.register("/service-worker.js")</script>'

def html(body,classes=''):
    return f'<!doctype html><html data-theme="dark" data-bs-theme="dark" data-mobile-nav-position="bottom" class="{classes}"><head><meta name="viewport" content="width=device-width,initial-scale=1">{links}<style>{styles}</style></head><body data-user-id="1" class="{classes}">{body}{scripts}</body></html>'
home=html(f'<nav class="navbar navbar-top fixed-top p-0" id="navbar-top">{nav}</nav><main id="page-content-target"><form class="search-form"><input class="search-input" type="search" placeholder="Search"></form><article class="post-card" data-post-id="p1"><img id="offline-photo" src="/static/images/default_group.jpg" width="120" height="120"><img id="lazy-photo" data-src="/static/images/default_group.jpg" src="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%271%27 height=%271%27/%3E" width="120" height="120"><p>Saved post</p></article><div style="height:1800px"></div></main><div class="mobile-bottom-nav"><a href="/" class="nav-chip active">Home</a><a href="/groups/dashboard/" class="nav-chip">Groups</a></div>')
chatpage=html(f'<main id="page-content-target">{chat}<div class="voice-note-bubble"><button class="vn-play-btn vn-download-mode"><i class="bi bi-arrow-down vn-play-icon"></i></button><audio class="d-none" data-src="/test-audio.wav" preload="none"></audio></div></main>','in-conversation-active')

class LayoutFixture(Fixture):
    def do_GET(self):
        from urllib.parse import urlsplit
        path=urlsplit(self.path).path
        if path=='/test-audio.wav':
            import io, wave
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000);audio.writeframes(b'\x00\x00'*24000)
            return self.respond(buffer.getvalue(),'audio/wav')
        if path=='/':return self.respond(home.encode(),'text/html',owner='1',policy='rendered-v1')
        if path=='/messaging/conversation/12/':return self.respond(chatpage.encode(),'text/html',owner='1',policy='rendered-v1')
        return super().do_GET()

def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),LayoutFixture);threading.Thread(target=server.serve_forever,daemon=True).start()
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(viewport={'width':390,'height':844});page=ctx.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        origin=f'http://127.0.0.1:{server.server_port}'
        def saved(path):
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if page.evaluate("""async path=>{const r=await(await caches.open('vasto-private-pages-v1-1')).match(location.origin+path);return !!r&& (await r.text()).includes('vasto-scroll')}""",path):
                    return
                page.wait_for_timeout(100)
            raise AssertionError('Snapshot was not committed: '+path)
        def dom_structure():
            # Security-only script/CSRF removal is intentional; all UI elements
            # must retain their original hierarchy, IDs and sibling order.
            return page.evaluate('''()=>{const nodes=[...document.querySelectorAll('#page-content-target *:not(script):not(style):not(input[name="csrfmiddlewaretoken"])')];return nodes.map(node=>[node.tagName,node.id,nodes.indexOf(node.parentElement)]);}''')
        def geometry(selectors):
            return page.evaluate('''selectors=>Object.fromEntries(selectors.map(selector=>{const el=document.querySelector(selector);if(!el)return[selector,null];const r=el.getBoundingClientRect(),s=getComputedStyle(el);return[selector,{x:r.x,y:r.y,w:r.width,h:r.height,display:s.display,padding:s.padding,borderRadius:s.borderRadius}]}))''',selectors)
        page.goto(origin);page.wait_for_function('!!navigator.serviceWorker.controller');page.reload();page.wait_for_load_state('networkidle');saved('/')
        selectors=['.search-form','.search-input','#navbar-top','#navbar-top .dropdown','#navbar-top .avatar-link','.mobile-bottom-nav']
        before=geometry(selectors)
        original_dom=dom_structure()
        ctx.set_offline(True);page.goto(origin+'/?offline_saved=1');page.get_by_text('You are offline viewing cached pages',exact=True).wait_for();page.wait_for_load_state('load')
        after=geometry(selectors)
        assert dom_structure()==original_dom,'Offline UI element hierarchy differs from the live DOM'
        assert before==after,{'before':before,'after':after}
        assert page.locator('form.search-form input').count()==1
        assert page.locator('[data-offline-indicator]').inner_text()=='You are offline viewing cached pages'
        assert page.locator('#offline-photo').evaluate('image=>image.complete&&image.naturalWidth>0')
        assert page.locator('#lazy-photo').evaluate('image=>image.complete&&image.naturalWidth>1')
        checks+=['Original header/search/profile/theme/navigation geometry unchanged','Image bytes available offline','Only the requested date-free offline label']
        page.get_by_role('button',name='Theme Toggle',exact=True).click()
        assert page.locator('#navbar-top .dropdown-menu.show').count()==1
        page.locator('[data-offline-theme="light"]').click()
        assert page.locator('html').get_attribute('data-theme')=='light'
        checks.append('Original theme menu works offline')
        page.evaluate('scrollTo(0,500)');page.wait_for_timeout(250)
        assert page.locator('.mobile-bottom-nav').evaluate("node=>node.classList.contains('vasto-scroll-hidden')")
        page.evaluate('scrollTo(0,200)');page.wait_for_timeout(250)
        assert not page.locator('.mobile-bottom-nav').evaluate("node=>node.classList.contains('vasto-scroll-hidden')")
        checks.append('Original navigation hides and returns with scrolling')
        page.wait_for_timeout(300);page.reload();page.get_by_text('You are offline viewing cached pages',exact=True).wait_for()
        page.wait_for_function('Math.abs(window.scrollY-200)<2')
        checks.append('Offline page reload restores the saved scroll position')
        ctx.set_offline(False);page.goto(origin+'/messaging/conversation/12/');page.wait_for_load_state('networkidle');saved('/messaging/conversation/12/')
        selectors=['.pwanimate-composer-wrapper','#messaging-composer-form','#messageInput','#chatBackBtn','#attachBtn','#emojiBtn','#voiceBtn']
        before=geometry(selectors)
        original_dom=dom_structure()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if page.evaluate('''async()=>{const response=await(await caches.open('vasto-private-pages-v1-1')).match(location.origin+'/messaging/conversation/12/');return response&&(await response.text()).includes('data-offline-media-ready="true"')}'''):break
            page.wait_for_timeout(100)
        else:raise AssertionError('Voice message was not saved')
        ctx.set_offline(True);page.goto(origin+'/messaging/conversation/12/?offline_saved=1');page.get_by_text('You are offline viewing cached pages',exact=True).wait_for();page.wait_for_load_state('load')
        after=geometry(selectors)
        assert dom_structure()==original_dom,'Offline UI element hierarchy differs from the live DOM'
        assert before==after,{'before':before,'after':after}
        assert page.locator('#messaging-composer-form textarea#messageInput').count()==1
        checks.append('Original chat composer DOM, padding, shape and button positions unchanged')
        checks.append('Home and chat snapshots preserve UI element hierarchy, IDs and sibling order')
        page.locator('.vn-play-btn').click()
        page.wait_for_function("()=>document.querySelector('.voice-note-bubble audio').currentTime>0")
        assert page.locator('.vn-play-icon').evaluate("node=>node.classList.contains('bi-pause-fill')")
        checks.append('Hidden data-src voice message cached and original play button works offline')
        assert not errors,errors
        browser.close()
    server.shutdown();print(json.dumps({'passed':checks,'page_errors':errors},indent=2))

if __name__=='__main__':main()
