"""Compare actual Django page DOM and per-frame shell geometry during HTMX navigation."""
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','pwaninet.settings.local')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import override_settings
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[2]

def pages():
    user=NS(id=7,pk=7,username='qa',first_name='QA',is_authenticated=True,is_staff=False,is_superuser=False,profile_pic=None,get_full_name=lambda:'QA')
    result={}
    with override_settings(CACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}):
        for path,template,name,app in [('/','base.html','home','posts'),('/messaging/','messaging/conversation_list.html','conversation_list','messaging')]:
            request=NS(user=user,path=path,GET={},resolver_match=NS(url_name=name,app_name=app),COOKIES={},META={})
            result[path]=render_to_string(template,{'csrf_token':'fixture-token','app_version':'test','request':request,'user':user,'conversations':[],'conversation_data':[],'suggested_users':[],'unmessaged_friends':[]})
    return result

def main():
    rendered=pages()
    manager=next(script for script in re.findall(r'<script[^>]*>(.*?)</script>',rendered['/'],re.S) if 'Navigation Scroll & Feed Position Manager' in script)
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
        for position in ['bottom','top']:
            context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            page=context.new_page();errors=[];requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:requests.append((request.resource_type,urlsplit(request.url).path)))
            def route(request):
                path=urlsplit(request.request.url).path
                if path in rendered:
                    # Keep the real shell, list markup, inline CSS and navigation
                    # manager. Unrelated services are excluded from this isolated fixture.
                    html=re.sub(r'<script\b[^>]*>.*?</script>','',rendered[path],flags=re.S|re.I)
                    html=html.replace('<html ',f'<html data-mobile-nav-position="{position}" ')
                    html=html.replace('</body>','<script src="/static/js/htmx.min.js"></script><script>'+manager+'</script></body>')
                    return request.fulfill(body=html,content_type='text/html')
                file=ROOT/path.lstrip('/')
                if path.endswith('conversation-detail.css'):time.sleep(0.7)
                if path.startswith('/static/') and file.is_file():return request.fulfill(path=str(file))
                request.fulfill(body='{}',content_type='application/json')
            page.route('**/*',route)
            page.add_init_script('''window.navigationFrames=[];window.trackNavigation=false;
                function sample(){if(window.trackNavigation){const nav=document.querySelector('.mobile-bottom-nav'),header=document.querySelector('#navbar-top');navigationFrames.push({y:nav.getBoundingClientRect().y,h:header.getBoundingClientRect().height,nested:document.querySelectorAll('#page-content-target #navbar-top,#page-content-target .mobile-bottom-nav').length});}requestAnimationFrame(sample);}requestAnimationFrame(sample);''')
            origin='http://localhost:8123'
            def native():
                page.evaluate("document.body.classList.add('is-native-app');document.documentElement.style.setProperty('--vasto-safe-area-top','28px');document.documentElement.style.setProperty('--vasto-safe-area-bottom','24px')")
            def fingerprint():
                return page.evaluate('''()=>[...document.querySelectorAll('#page-content-target *:not(script):not(style):not(link)')].map(node=>[node.tagName,node.id,[...node.classList].filter(name=>!name.startsWith('htmx-')).sort().join(' '),node.parentElement.id||node.parentElement.tagName])''')
            def geometry():
                return page.evaluate('''()=>Object.fromEntries(['#navbar-top','.mobile-bottom-nav','#app-shell-layout','#page-content-target','.page-wrap','.page-header'].map(selector=>{const node=document.querySelector(selector);if(!node)return[selector,null];const r=node.getBoundingClientRect();return[selector,{x:r.x,y:r.y,w:r.width,h:r.height}]}))''')
            page.goto(origin+'/messaging/');page.wait_for_load_state('networkidle');native()
            direct_dom=fingerprint();direct_geometry=geometry()
            assert not page.locator('#app-shell-layout').evaluate("node=>node.classList.contains('in-conversation-shell')")
            page.goto(origin+'/');page.wait_for_load_state('networkidle');native()
            initial=geometry();requests.clear();page.evaluate('window.trackNavigation=true')
            page.locator('.mobile-bottom-nav a[aria-label="Chats"]').click()
            page.wait_for_selector('#messagingTabs');page.wait_for_load_state('networkidle');page.wait_for_timeout(150)
            frames=page.evaluate('window.navigationFrames')
            assert len(frames)>5,frames
            assert not [r for r in requests if r[0]=='document'],requests
            assert len([r for r in requests if r==('xhr','/messaging/')])==1,requests
            assert not page.locator('body').evaluate("node=>node.classList.contains('pwanimate-active')")
            assert page.locator('body').evaluate("node=>node.classList.contains('is-native-app')")
            assert all(frame['nested']==0 and abs(frame['y']-initial['.mobile-bottom-nav']['y'])<1 and abs(frame['h']-initial['#navbar-top']['h'])<1 for frame in frames),frames
            assert fingerprint()==direct_dom,'Swapped conversation-list DOM differs from direct navigation'
            assert geometry()==direct_geometry,{'swapped':geometry(),'direct':direct_geometry}
            # Browser history must reuse the same shell without inserting another navbar.
            page.go_back();page.wait_for_timeout(300)
            assert page.locator('#navbar-top').count()==1 and page.locator('.mobile-bottom-nav').count()==1
            assert not errors,errors
            checks.append(f'{position} navigation: one request, stable header/nav on every frame, exact list DOM and geometry, native state retained')
            context.close()
        browser.close()
    print(json.dumps({'passed':checks},indent=2))

if __name__=='__main__':main()
