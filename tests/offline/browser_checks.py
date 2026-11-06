"""Real Chromium + service-worker fixture tests; no production account required.
Run from repository root: .venv/bin/python tests/offline/browser_checks.py
"""
import json
import subprocess
from pathlib import Path
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIDEO = Path('/tmp/vasto-offline-test.mp4')

class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    fail_routes = set()
    chat_version = 1
    notification_version = 1
    def do_POST(self):
        if urlsplit(self.path).path == '/offline/notification-access/':
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            return self.respond(json.dumps({'notifications': [{'id': value, 'html': f'<div class="notif-item" data-notification-id="{value}">Retained notification</div>'} for value in data['ids']]}).encode(), 'application/json', owner='1')
        if urlsplit(self.path).path == '/offline/message-access/':
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            return self.respond(json.dumps({'messages': [{'id': int(value), 'conversation': int(data['conversation']), 'content': f'Message {value}', 'created_at': '2026-11-06T10:00:00Z', 'is_deleted': False, 'edited_at': None} for value in data['ids']]}).encode(), 'application/json', owner='1')
        if urlsplit(self.path).path == '/offline/post-access/':
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            return self.respond(json.dumps({'posts': [{'id': value, 'downloadable': True} for value in data['ids']]}).encode(), 'application/json', owner='2' if 'account=2' in self.headers.get('Cookie', '') else '1')
        self.do_GET()
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/groups/44/chat/':
            self.send_response(302)
            self.send_header('Location', '/messaging/conversation/1/')
            self.end_headers()
            return
        if path == '/service-worker.js':
            return self.send_file(ROOT / 'static/service-worker.js', 'application/javascript')
        if path.startswith('/static/'):
            file = ROOT / path.lstrip('/')
            if file.is_file():
                kind = 'application/javascript' if file.suffix == '.js' else 'text/css' if file.suffix == '.css' else 'image/svg+xml'
                return self.send_file(file, kind)
            return self.respond(b'', 'text/plain', status=404)
        if path == '/test-video.mp4':
            return self.send_file(VIDEO, 'video/mp4')
        if path == '/interrupted.mp4':
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', '100000')
            self.end_headers()
            self.wfile.write(b'incomplete')
            self.close_connection = True
            return
        if path in self.fail_routes: return self.respond(b'unavailable', 'text/plain', status=503)
        owner = '2' if 'account=2' in self.headers.get('Cookie', '') else '1'
        if path == '/api/slow-account/':
            time.sleep(1.5)
            return self.respond(json.dumps({'owner': owner}).encode(), 'application/json', owner=owner)
        if path == '/users/logout/': owner = ''
        if path == '/api/error/': return self.respond(b'error', 'text/plain', status=503)
        if path == '/post/00000000-0000-0000-0000-000000000403/': return self.respond(b'denied', 'text/html', status=403, owner=owner)
        html = f'''<!doctype html><html><head><title>Fixture</title></head><body data-user-id="{owner}"><nav><a href="/">Feed</a><a href="/documents/">Documents</a></nav><main><h1>Account {owner}</h1><div id="posts"><article class="post-card" data-post-id="p1">Post one <video data-post-id="p1" poster="/static/test-poster.svg"></video><button data-media-type="video" data-download-allowed="true" data-url="/test-video.mp4">Download</button></article></div><form><input name="csrfmiddlewaretoken" value="SECRET_CSRF"></form><script>window.SECRET_CSRF='TOKEN_IN_SCRIPT';</script></main><script src="/static/js/offline-capture.js" defer></script><script>navigator.serviceWorker.register('/service-worker.js');</script></body></html>'''
        if path.startswith('/messaging/conversation/'):
            html = html.replace('<main>', f'<main><div id="messagesContainer"><div class="message-wrapper" data-message-id="10">Previous message</div>{"<div class=message-wrapper data-message-id=12>New synced message</div>" if self.chat_version == 2 else ""}</div><script src="/static/js/messaging/offline-cache.js"></script>')
        if path == '/notifications/':
            html = html.replace('<main>', '<main><div id="notification-list"><div class="card-body"><div class="notif-item" data-notification-id="00000000-0000-0000-0000-000000000001">Previous notification</div></div></div>')
        if path == '/notifications/' and self.notification_version == 2:
            html = html.replace('<div class="card-body">', '<div class="card-body"><div class="notif-item" data-notification-id="00000000-0000-0000-0000-000000000003">New synced notification</div>')
        self.respond(html.encode(), 'text/html', owner=owner, policy='rendered-v1')
    def send_file(self, path, kind):
        if not path.exists(): return self.respond(b'', 'text/plain', status=404)
        self.respond(path.read_bytes(), kind)
    def respond(self, data, kind, status=200, owner=None, policy=None):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'private, no-cache')
        if owner is not None: self.send_header('X-Vasto-Account', owner)
        if policy: self.send_header('X-Vasto-Offline', policy)
        if urlsplit(self.path).path == '/messaging/conversation/1/': self.send_header('X-Vasto-Group', '44')
        self.end_headers()
        self.wfile.write(data)


def main():
    if not VIDEO.exists():
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        'color=c=blue:s=160x90:r=24', '-t', '3', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(VIDEO)], check=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox'])
        context = browser.new_context()
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin)
        page.wait_for_function('!!navigator.serviceWorker.controller')
        page.reload()
        def poll(expression, arg=None):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if page.evaluate(expression, arg): return
                page.wait_for_timeout(100)
            raise AssertionError('Timed out: ' + expression)
        def saved(path='/'):
            poll('''async path => { const names = await caches.keys(); const name = names.find(n => n.startsWith('vasto-private-pages-v1-')); return name && !!await (await caches.open(name)).match(location.origin + path); }''', arg=path)
        saved()
        checks.append('eligible rendered page saved under account 1')
        page.evaluate('''() => {
            const posts = document.querySelector('#posts');
            posts.insertAdjacentHTML('beforeend', '<article class="post-card" data-post-id="p2">Post two</article><article class="post-card" data-post-id="p3">Post three</article><article class="post-card" data-post-id="p2">DUPLICATE</article>');
        }''')
        poll('''async () => { const response = await (await caches.open('vasto-private-pages-v1-1')).match(location.origin + '/'); return response && (await response.text()).includes('Post three'); }''')
        checks.append('rendered pagination persisted after DOM update')
        if VIDEO.exists():
            page.get_by_role('button', name='Save for offline', exact=True).click()
            page.get_by_role('button', name='Remove offline video').wait_for()
            poll('''async () => { const r = await (await caches.open('vasto-private-pages-v1-1')).match(location.origin + '/'); return r && (await r.text()).includes('data-offline-download-id="p1"'); }''')
            checks.append('MP4 bytes and metadata committed before ready state')
        page.goto(origin + '/pwanimate/00000000-0000-0000-0000-000000000001/')
        saved('/pwanimate/00000000-0000-0000-0000-000000000001/')
        page.goto(origin + '/documents/')
        saved('/documents/')
        page.wait_for_timeout(1200)
        await_text = page.evaluate('''async () => (await (await (await caches.open('vasto-private-pages-v1-1')).match(location.origin + '/')).text())''')
        assert 'SECRET_CSRF' not in await_text and 'TOKEN_IN_SCRIPT' not in await_text
        assert 'DUPLICATE' not in await_text
        checks.append('CSRF values, original scripts and duplicate post IDs excluded')
        context.set_offline(True)
        page.goto(origin + '/?offline_saved=1')
        page.get_by_text('End of saved content. Connect to load more.').wait_for()
        assert page.locator('.post-card').count() == 3
        assert page.locator('.post-card').all_text_contents() == ['Post one Download', 'Post two', 'Post three']
        checks.append('offline navigation restores all loaded posts in order')
        if VIDEO.exists():
            assert page.evaluate('''async () => { const r = await fetch('/offline-media-stream/p1', {headers:{Range:'bytes=2-9'}}); return r.status === 206 && (await r.arrayBuffer()).byteLength === 8; }''')
            assert page.evaluate('''async () => (await fetch('/offline-media-stream/p1', {headers:{Range:'bytes=999999999-'}})).status''') == 416
            page.locator('video').evaluate('v => { v.muted = true; return v.play(); }')
            poll('() => document.querySelector("video").currentTime > 0.1')
            page.locator('video').evaluate('v => v.currentTime = 1.5')
            poll('() => document.querySelector("video").currentTime >= 1.5')
            checks.append('saved MP4 plays and seeks offline; byte range/416 checks pass')
        page.goto(origin + '/pwanimate/00000000-0000-0000-0000-000000000001/')
        page.get_by_text('End of saved content. Connect to load more.').wait_for()
        checks.append('visited Pwanimate conversation opens offline')
        page.goto(origin + '/documents/')
        page.get_by_text('End of saved content. Connect to load more.').wait_for()
        page.go_back()
        assert '/pwanimate/' in page.url
        checks.append('saved route history/back navigation works')
        page.goto(origin + '/groups/999/')
        page.get_by_text('This page needs an initial online visit').wait_for()
        checks.append('unvisited route renders inline offline state')
        context.set_offline(False)
        page.goto(origin + '/messaging/conversation/1/')
        saved('/messaging/conversation/1/')
        page.evaluate('''async () => {
            await window.offlineCache.saveMessages([
                {id: 3, conversation: 1, content: 'newest', created_at: '2026-11-06T10:03:00Z'},
                {id: 1, conversation: 1, content: 'oldest', created_at: '2026-11-06T10:01:00Z'},
                {id: 2, conversation: 1, content: 'middle', created_at: '2026-11-06T10:02:00Z'}
            ]);
        }''')
        assert page.evaluate('''async () => (await offlineCache.getMessages(1)).map(m => m.id)''') == [1, 2, 3]
        assert page.evaluate('''async () => {
            window.IS_GROUP_CHAT = true;
            await offlineCache.saveMessages([{id:1,group:1,content:'group',created_at:'2026-11-06T10:00:00Z'}]);
            const group = await offlineCache.getMessages(1);
            window.IS_GROUP_CHAT = false;
            const direct = await offlineCache.getMessages(1);
            return group.length === 1 && group[0].content === 'group' && direct.length === 3 && direct[0].content === 'oldest';
        }''')
        checks.append('message transactions commit in order and group/direct IDs stay isolated')
        page.evaluate('''() => document.querySelector('#messagesContainer').insertAdjacentHTML('afterbegin', '<div class="message-wrapper" data-message-id="9">Older scrolled message</div>')''')
        poll('''async () => { const r = await (await caches.open('vasto-private-pages-v1-1')).match(location.href); return r && (await r.text()).includes('Older scrolled message'); }''')
        context.set_offline(True)
        page.goto(origin + '/messaging/conversation/1/')
        page.get_by_text('Previous message', exact=True).wait_for()
        page.get_by_text('Older scrolled message', exact=True).wait_for()
        checks.append('conversation opens offline with current and older loaded messages')
        page.goto(origin + '/groups/44/chat/')
        page.get_by_text('Older scrolled message', exact=True).wait_for()
        checks.append('saved group gateway opens the canonical conversation offline')
        Fixture.chat_version = 2
        context.set_offline(False)
        page.get_by_text('New synced message', exact=True).wait_for()
        checks.append('saved conversation automatically resumes online and receives new messages')
        page.goto(origin + '/notifications/')
        saved('/notifications/')
        page.evaluate('''() => document.querySelector('#notification-list .card-body').insertAdjacentHTML('beforeend', '<div class="notif-item" data-notification-id="00000000-0000-0000-0000-000000000002">Older notification</div>')''')
        poll('''async () => { const r = await (await caches.open('vasto-private-pages-v1-1')).match(location.href); return r && (await r.text()).includes('Older notification'); }''')
        context.set_offline(True)
        page.reload()
        page.get_by_text('Older notification', exact=True).wait_for()
        page.get_by_text('Previous notification', exact=True).wait_for()
        checks.append('notification history and loaded pagination open offline')
        Fixture.notification_version = 2
        context.set_offline(False)
        page.get_by_text('New synced notification', exact=True).wait_for()
        checks.append('saved notifications automatically refresh when online')
        context.set_offline(False)
        page.evaluate("() => { window.lateAccountResponse = fetch('/api/slow-account/').catch(() => {}); }")
        page.wait_for_timeout(100)
        context.add_cookies([{'name': 'account', 'value': '2', 'url': origin}])
        page.goto(origin)
        saved()
        page.wait_for_timeout(1700)
        assert page.evaluate("async () => (await (await (await caches.open('vasto-private-state-v1')).match(location.origin + '/__offline_state__')).json()).owner") == '2'
        checks.append('late pre-switch response cannot restore the previous account')
        assert 'vasto-private-pages-v1-1' not in page.evaluate('caches.keys()')
        assert not any(item['name'].startswith('vasto_chat_v2_1_') for item in page.evaluate('indexedDB.databases()'))
        checks.append('account switch purges account 1 snapshots and message databases')
        page.goto(origin + '/users/logout/')
        context.set_offline(True)
        page.goto(origin)
        page.get_by_text('This page needs an initial online visit').wait_for()
        checks.append('logout prevents reopening prior managed pages')
        context.set_offline(False)
        context.clear_cookies()
        page.goto(origin)
        saved()
        # A new successful feed response keeps pages previously loaded by scrolling.
        page.evaluate('''() => document.querySelector('#posts').insertAdjacentHTML('beforeend', '<article class="post-card" data-post-id="p2">Post two</article>')''')
        poll('''async () => { const r = await (await caches.open('vasto-private-pages-v1-1')).match(location.origin + '/'); return r && (await r.text()).includes('Post two'); }''')
        page.reload()
        poll('''async () => { const r = await (await caches.open('vasto-private-pages-v1-1')).match(location.origin + '/'); return r && (await r.text()).includes('Post two'); }''')
        page.wait_for_timeout(1500)
        assert page.evaluate('''async () => (await (await (await caches.open('vasto-private-pages-v1-1')).match(location.origin + '/')).text()).includes('Post two')''')
        checks.append('successful feed refresh retains authorized earlier pages')
        Fixture.fail_routes.add('/')
        page.goto(origin)
        page.get_by_text('End of saved content. Connect to load more.').wait_for()
        assert 'Post two' in page.locator('body').inner_text()
        checks.append('server 503 refresh preserves last good content')
        Fixture.fail_routes.clear()
        page.goto(origin)
        saved()
        if VIDEO.exists():
            button = page.get_by_role('button', name='Save for offline', exact=True)
            button.wait_for()
            page.evaluate('''() => document.querySelector('[data-url]').dataset.url = '/interrupted.mp4' ''')
            dialogs = []
            page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            button.click()
            poll('''() => document.querySelector('.offline-save-button')?.disabled === false''')
            assert dialogs
            assert page.evaluate('''async () => { const db = await new Promise(resolve => {const r=indexedDB.open('vasto_offline_media_1',1);r.onsuccess=()=>resolve(r.result)}); return await new Promise(resolve=>{const r=db.transaction('files').objectStore('files').getAll();r.onsuccess=()=>{resolve(r.result.length===0);db.close();}}); }''')
            checks.append('interrupted download never creates ready media metadata')
        # Fresh browser context has no hosted cache; the local launch document is self-contained.
        assert 'https://pwaninet.app' in (ROOT / 'www/index.html').read_text()
        assert not json.loads((ROOT / 'capacitor.config.json').read_text())['server'].get('url')
        checks.append('release config starts from bundled assets')
        browser.close()
    server.shutdown()
    print(json.dumps({'passed': checks, 'page_errors': errors}, indent=2))

if __name__ == '__main__': main()
