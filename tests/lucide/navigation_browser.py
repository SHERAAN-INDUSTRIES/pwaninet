"""Actual shared Django templates + local Chrome; no server, DB or provider access.

Run: venv/bin/python tests/lucide/navigation_browser.py
Only relevant inline controllers execute; unrelated services are excluded.
"""
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.sals_wp4.ui_settings')
import django
django.setup()
from django import forms
from django.template import Context, Template
from playwright.sync_api import sync_playwright


class ExampleForm(forms.Form):
    title = forms.CharField(label='Title', required=True)


def rendered(path='/', authenticated=True, staff=False):
    user = NS(id=7, pk=7, username='qa', first_name='QA', is_authenticated=authenticated,
              is_staff=staff, is_superuser=staff, profile_pic=None,
              get_full_name=lambda: 'QA', is_two_factor_enabled=True)
    group = path.startswith('/groups')
    request = NS(user=user, path=path, GET={}, COOKIES={}, META={},
                 resolver_match=NS(url_name='groups_dashboard' if group else 'home',
                                   app_name='groups' if group else 'posts', namespace='groups' if group else 'posts'))
    form = ExampleForm(data={})
    context = {'app_version': 'wp2-test', 'request': request, 'user': user, 'field': form['title'],
               'fixture_label': 'Groups' if group else 'Home', 'unread_messages_count': 2,
               'unread_notifications_count': 3, 'has_unread_group_activity': True}
    html = Template('{% extends "base.html" %}{% block content %}<h1 id="fixture-route">{{ fixture_label }}</h1>{% include "partials/_form_field.html" %}{% endblock %}').render(Context(context))
    # Retain actual shell/inline styles and the controllers affected by WP2.
    # Services, media managers and unrelated app controllers are not executed.
    def script(match):
        attrs, code = match.group(1), match.group(2)
        if any(name in attrs for name in ['js/htmx.min.js', 'js/bootstrap.bundle.min.js',
                                          'vendor/lucide/lucide.min.js', 'js/vasto-icons.js']):
            return match.group()
        if any(marker in code for marker in ['Navigation Scroll & Feed Position Manager', 'function setTheme(',
                                             'function copyApkLink(', 'window.showGuestAuthPrompt =', 'window.isAuthenticated =']):
            return match.group()
        if 'function showChatNotificationBanner(' in code:
            # Use the exact production functions, excluding WebSockets/polling.
            banner = code[code.index('    function showChatNotificationBanner('):code.index('    function updateNotificationCount(')]
            toast = code[code.index('    function showNotification('):code.index('    function showWebSocketError(')]
            return '<script>' + banner + toast + '</script>'
        return ''
    return re.sub(r'<script\b([^>]*)>(.*?)</script>', script, html, flags=re.S | re.I)


def main():
    pages = {'/': rendered(), '/groups/dashboard/': rendered('/groups/dashboard/'),
             '/staff/': rendered(staff=True), '/guest/': rendered(authenticated=False)}
    checks = []
    def passed(name):
        checks.append(name)
        print('PASS ' + name, flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        if os.environ.get('WP2_BROWSER_DEBUG'):
            page.on('console', lambda message: print('CONSOLE',message.type,message.text,flush=True))
            page.on('requestfailed', lambda request: print('REQUEST FAILED',request.url,request.failure,flush=True))
        def route(r):
            url = urlsplit(r.request.url)
            if url.hostname != 'wp2.test':
                return r.abort()
            if url.path in pages:
                return r.fulfill(body=pages[url.path], content_type='text/html')
            file = ROOT / url.path.lstrip('/')
            if url.path.startswith('/static/') and file.is_file():
                return r.fulfill(path=str(file))
            return r.fulfill(body='{}', content_type='application/json')
        page.route('**/*', route)
        page.goto('http://wp2.test/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        assert page.locator('i.vasto-icon').count() == 0
        assert page.locator('.vasto-icon').count() >= 40
        assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0
        assert page.locator('.vasto-icon.bi').count() == 0
        assert page.locator('.invalid-feedback svg[data-lucide="triangle-alert"]').count() == 1
        passed('initial shared navigation, validation glyphs, registry rendering and decorative accessibility')
        assert page.locator('#left-nav-rail a.active').first.inner_text().strip() == 'Home'
        assert page.locator('#left-nav-rail a[href*="messaging"]').get_attribute('data-bypass-htmx') == 'true'
        assert page.locator('#left-nav-rail a[href*="messaging"]').get_attribute('hx-boost') == 'false'
        assert page.locator('.avatar-link').get_attribute('aria-label') == 'Profile'
        passed('navigation destinations, messaging bypass hooks and profile accessible name')
        if os.environ.get('WP2_SCREENSHOTS'):
            page.screenshot(path='/tmp/wp2-desktop.png')
        assert page.evaluate('''()=>{const nodes=[...document.querySelectorAll('svg.vasto-icon')];VastoIcons.init();VastoIcons.render(document);return nodes.every(n=>n.isConnected)&&nodes.length===document.querySelectorAll('svg.vasto-icon').length}''')
        passed('repeated render/init preserves SVG identity without duplicates')
        for width in [1440, 900, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(100)
            assert page.locator('#left-nav-rail').is_visible() == (width >= 992)
            assert page.locator('.mobile-bottom-nav').is_visible() == (width < 768)
            geometry = page.locator('svg.vasto-icon:visible').evaluate_all('''nodes=>nodes.map(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return {width:parseFloat(s.width),height:parseFloat(s.height),right:r.right,left:r.left}})''')
            assert all(n['width'] in [16,20,24] and n['height'] == n['width'] for n in geometry), geometry
            assert all(n['left'] >= -1 and n['right'] <= width+1 for n in geometry), geometry
            if width < 768:
                assert page.locator('.mobile-bottom-nav a[aria-label="Home"] svg').evaluate('n=>getComputedStyle(n).transform') != 'none'
                centers = page.locator('.mobile-bottom-nav .nav-chip').evaluate_all('''nodes=>nodes.map(n=>{const r=n.querySelector('svg,img').getBoundingClientRect();return r.y+r.height/2})''')
                assert max(centers)-min(centers) <= 3, centers
            if width == 390 and os.environ.get('WP2_SCREENSHOTS'):
                page.screenshot(path='/tmp/wp2-mobile.png')
            passed(f'{width}px: responsive rails/tabs, square standard sizes, no horizontal clipping, active mobile transform')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        for theme in ['light', 'dark', 'system']:
            page.evaluate('(t)=>setTheme(t,false)', theme)
            assert page.evaluate('''()=>[...document.querySelectorAll('#left-nav-rail svg.vasto-icon, #navbar-top svg.vasto-icon')].every(n=>getComputedStyle(n).stroke===getComputedStyle(n.parentElement).color)''')
        passed('light/dark/system theme foreground inheritance')
        page.get_by_role('button', name='Theme Toggle').click()
        assert page.locator('#navbar-top .dropdown-menu.show').count() == 1
        assert page.locator('#navbar-top .dropdown-menu.show svg').count() == 3
        page.get_by_role('button', name='Dark', exact=True).click()
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        passed('real Bootstrap theme dropdown opens and selections work')
        page.locator('#left-nav-rail a[href="/groups/dashboard/"]').click()
        try:
            page.wait_for_function('document.querySelector("#fixture-route").textContent==="Groups"', timeout=10000)
        except Exception:
            print('Navigation errors:', errors, flush=True)
            raise
        assert page.locator('#navbar-top').count() == 1
        assert page.locator('#left-nav-rail').count() == 1
        assert page.locator('i.vasto-icon').count() == 0
        assert page.locator('#left-nav-rail a[href="/groups/dashboard/"]').get_attribute('class').find('active') >= 0
        assert 'active' not in page.locator('#left-nav-rail a[href="/"]').get_attribute('class').split()
        assert page.locator('#context-rail-target svg.vasto-icon').count() == 5
        passed('real htmx full-document navigation replaces and renders navbar, side rails, context and form')
        page.reload()
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        assert page.locator('i.vasto-icon').count() == 0
        passed('full-page reload renders shared controls')
        page.evaluate('''()=>{const fragment=document.createElement('div');fragment.id='wp2-dynamic';fragment.innerHTML='<i data-lucide="search" class="vasto-icon" aria-hidden="true"></i>';document.querySelector('#page-content-target').append(fragment);fragment.dispatchEvent(new CustomEvent('htmx:afterSwap',{bubbles:true,detail:{target:fragment,xhr:{status:200,responseURL:location.href}}}))}''')
        assert page.locator('#wp2-dynamic svg').count() == 1
        passed('dynamic fragment renders through existing htmx lifecycle')
        # CSS opt-in must not alter the existing Spark styling.
        page.evaluate('''()=>{document.body.classList.add('pwanimate-active');const i=document.createElement('i');i.dataset.lucide='brain';i.id='spark-probe';document.body.append(i);VastoIcons.render(i)}''')
        # A Spark child can reload pwanimate.css after the shared stylesheet.
        page.evaluate("document.head.append(document.getElementById('pwanimate-css'))")
        assert page.locator('#spark-probe').evaluate('n=>getComputedStyle(n).strokeWidth') == '1.75px'
        assert page.locator('#left-nav-rail svg.vasto-icon').first.evaluate('n=>getComputedStyle(n).width') == '24px'
        page.evaluate("document.body.classList.remove('pwanimate-active')")
        passed('shared sizing stays scoped and Spark icon stroke/size rules remain intact')
        page.evaluate("showChatNotificationBanner({conversation_id:1,conversation_type:'group',sender_name:'QA',message_preview:'New message'})")
        assert page.locator('#vasto-chat-floating-banner svg').count() == 2
        page.locator('.chat-banner-close').click()
        assert page.locator('#vasto-chat-floating-banner').count() == 0
        page.evaluate("showNotification({title:'QA',body:'Notice'})")
        assert page.locator('.notif-toast svg[data-lucide="x"]').count() == 1
        page.locator('.toast-close-btn').click()
        page.wait_for_timeout(350)
        assert page.locator('.notif-toast').count() == 0
        passed('dynamic notification/message banner Lucide icons and delegated dismiss controls')
        page.evaluate("bootstrap.Modal.getOrCreateInstance(document.getElementById('qrInstallModal')).show();document.execCommand=()=>true;copyApkLink()")
        assert page.locator('#qr-modal-copy-btn svg[data-lucide="check"]').count() == 1
        page.wait_for_timeout(2100)
        assert page.locator('#qr-modal-copy-btn svg[data-lucide="clipboard"]').count() == 1
        passed('install-link copied/restored icon states render without observers')
        page.goto('http://wp2.test/staff/')
        assert page.locator('#left-nav-rail svg[data-lucide="database"]').count() == 1
        assert page.locator('#left-nav-rail svg[data-lucide="shield-check"]').count() == 1
        passed('staff-only shared navigation icons render with existing conditions')
        page.goto('http://wp2.test/guest/')
        assert page.locator('.mobile-bottom-nav').count() == 0
        for name, expected in [('bi-heart-fill','heart'),('bi-chat-dots-fill','message-circle'),('bi-sliders','sliders-horizontal'),('unknown','sparkles')]:
            page.evaluate('(icon)=>showGuestAuthPrompt({icon})',name)
            assert page.locator('#guestAuthIcon').get_attribute('data-lucide') == expected
            assert page.locator('#guestAuthIcon').evaluate("n=>n.tagName==='svg'")
        passed('guest navigation and existing prompt API switches Lucide icons safely')
        assert not errors, errors
        passed('no browser JavaScript errors in exercised navigation controllers')
        browser.close()
    print(json.dumps({'passed': len(checks), 'failed': 0, 'skipped': 0}, indent=2))


if __name__ == '__main__':
    main()
