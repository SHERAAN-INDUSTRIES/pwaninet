import os,sys,re,json
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[2]));os.environ.setdefault('DJANGO_SETTINGS_MODULE','pwaninet.settings.local')
import django;django.setup()
from django.template.loader import render_to_string
from django.test import override_settings
from playwright.sync_api import sync_playwright
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'docs/verification/spark'
user=NS(id=7,pk=7,username='qa',first_name='QA',is_authenticated=True,is_staff=False,is_superuser=False,profile_pic=None,get_full_name=lambda:'QA')
request=NS(user=user,path='/pwanimate/settings/study/',GET={},resolver_match=NS(url_name='settings_study',app_name='pwanimate'),COOKIES={},META={})
ctx={'request':request,'user':user,'csrf_token':'test-token','app_version':'review','active_tab':'study','preferences':{'personal_instructions':''}}
with override_settings(CACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}):
 html=render_to_string('pwanimate/settings/index.html',ctx)
 modal=render_to_string('pwanimate/settings/partials/settings_modal.html',ctx)
source=(ROOT/'static/js/pwanimate/pwanimate-chat.js').read_text();start=source.index('    window.switchSparkSettingsTab = function');end=source.index('    window.setSparkModalActiveTab',start);tabs=source[start:end]
html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S|re.I).replace('</body>', '<script>'+tabs+'</script><script src="/static/js/spark-study-desk.js"></script></body>')
checks=[];created=[];methods=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
 page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 def route(r):
  path=urlsplit(r.request.url).path
  if path=='/pwanimate/settings/study/':return r.fulfill(body=html,content_type='text/html')
  if path.startswith('/api/pwanimate/'):
   methods.append((r.request.method,path))
   if r.request.method=='POST':created.append(r.request.post_data_json);return r.fulfill(json={'id':'fixture-reminder'})
   if r.request.method=='PATCH':return r.fulfill(json={'enabled':False})
   if r.request.method=='DELETE':return r.fulfill(json={'forgotten':True})
   payload={'memory/':{'enabled':True,'memories':[]},'reminders/':{'reminders':[]},'study-plans/':{'plans':[]}}.get(path.removeprefix('/api/pwanimate/'),{})
   return r.fulfill(json=payload)
  file=ROOT/path.lstrip('/')
  if path.startswith('/static/') and file.is_file():return r.fulfill(path=str(file))
  return r.fulfill(body='',status=404)
 page.route('**/*',route)
 for width,mode in [(320,'light'),(390,'dark'),(1280,'light')]:
  page.set_viewport_size({'width':width,'height':900});page.goto('http://localhost:8123/pwanimate/settings/study/')
  page.evaluate('mode=>{document.documentElement.dataset.theme=mode;document.documentElement.dataset.bsTheme=mode}',mode)
  page.get_by_text('A fresh start',exact=True).wait_for()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert page.locator('#mobile-tab-study').evaluate('(el)=>el.classList.contains("active")')
  assert page.locator('[data-study="enabled"]').is_checked()
  page.locator('#mobile-tab-usage').click();assert not page.locator('[data-study="reminder-form"]').is_visible()
  page.locator('#mobile-tab-study').click();assert page.locator('[data-study="reminder-form"]').is_visible()
  page.screenshot(path=str(OUT/f'settings-study-{width}-{mode}.png'),full_page=True)
  checks.append(f'{width}px {mode}: existing settings shell, scrollable tabs, state preserved, no page overflow')
 form=page.locator('[data-study="reminder-form"]')
 form.locator('[name="title"]').fill('Review settings form');form.locator('[name="due"]').fill('2026-11-10T10:30');form.locator('button').click()
 page.wait_for_function('document.querySelector("[data-study=reminder-form] input[name=title]").value === ""')
 assert created[0]['title']=='Review settings form'
 page.locator('[data-study="enabled"]').uncheck();page.wait_for_function('document.querySelector("[data-study=status]").textContent === "Memory paused."')
 page.on('dialog',lambda d:d.accept());page.locator('[data-study="forget"]').click()
 page.wait_for_function('document.querySelector("[data-study=status]").textContent === "Saved memory forgotten."')
 checks.append('Create reminder, toggle memory, and forget memory submit successfully through the settings pane')
 page.evaluate('(html)=>{document.querySelector("#pwanimate-workspace").remove();document.body.insertAdjacentHTML("beforeend",html);const m=document.querySelector("#pwanimateSettingsModal");m.style.display="block";m.classList.add("show");}',modal)
 page.evaluate("window.switchSparkSettingsTab('modal','study')")
 pane=page.locator('#pwanimate-modal-pane-study');pane.get_by_text('A fresh start',exact=True).wait_for()
 assert pane.locator('[data-study="enabled"]').is_checked()
 page.locator('#modal-tab-personalization').click();assert not pane.is_visible()
 page.locator('#modal-tab-study').click();assert pane.is_visible()
 page.wait_for_function('document.querySelector("#modal-tab-study").classList.contains("active") && getComputedStyle(document.querySelector("#pwanimate-modal-pane-study")).opacity === "1"')
 page.screenshot(path=str(OUT/'settings-study-desktop-modal.png'),full_page=True)
 checks.append('Actual desktop modal: pane initializes on open, switches correctly, and keeps scoped controls')
 assert not errors,errors
 b.close()
(OUT/'settings-study-browser.json').write_text(json.dumps({'checks':checks,'page_errors':errors,'api_data':'Synthetic empty account; real templates and scripts'},indent=2))
print(json.dumps(checks))
