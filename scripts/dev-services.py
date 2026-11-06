#!/usr/bin/env python3
"""Check configured Redis URLs and manage local Celery daemons with PID/log files."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit


def ping(url):
    import redis
    try:
        return redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2).ping()
    except redis.RedisError:
        return False


def ensure_redis(url, state, start):
    if ping(url):
        return
    address = urlsplit(url)
    port = address.port or 6379
    if not start or address.hostname not in ('localhost', '127.0.0.1') or address.password or address.username or address.scheme != 'redis':
        raise RuntimeError(f'Redis unreachable at {address.hostname}:{port}; check configured Redis settings.')
    # Reuse an existing local service; only start one when the configured endpoint is absent.
    import shutil
    if shutil.which('redis-server'):
        subprocess.run(['redis-server', '--bind', '127.0.0.1', '--port', str(port),
                        '--daemonize', 'yes', '--dir', str(state),
                        '--pidfile', str(state / f'redis-{port}.pid'),
                        '--logfile', str(state / f'redis-{port}.log')], check=True)
    elif shutil.which('docker'):
        name = f'vasto-dev-redis-{port}'
        existing = subprocess.run(['docker', 'container', 'inspect', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if existing.returncode == 0:
            subprocess.run(['docker', 'start', name], check=True)
        else:
            subprocess.run(['docker', 'run', '-d', '--name', name,
                            '-p', f'127.0.0.1:{port}:6379', 'redis:7-alpine'], check=True)
    else:
        raise RuntimeError('Install redis-server or start Docker, then rerun setup.')
    for _ in range(20):
        if ping(url):
            return
        time.sleep(.5)
    raise RuntimeError(f'Redis did not become ready on port {port}. Check the Redis log in {state}.')


def live_pid(path):
    try:
        pid = int(path.read_text().strip())
        if pid < 2:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'start'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--app', required=True)
    parser.add_argument('--settings', required=True)
    parser.add_argument('--no-start-redis', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ['DJANGO_SETTINGS_MODULE'] = args.settings
    import django
    django.setup()
    from django.conf import settings
    state = root / '.dev-services'
    state.mkdir(mode=0o700, exist_ok=True)
    urls = [settings.CELERY_BROKER_URL]
    result = settings.CELERY_RESULT_BACKEND
    if result.startswith(('redis://', 'rediss://')):
        urls.append(result)
    cache = settings.CACHES.get('default', {})
    if 'redis' in cache.get('BACKEND', '').lower():
        urls.append(cache['LOCATION'])
    for url in dict.fromkeys(urls):
        ensure_redis(url, state, args.action == 'start' and not args.no_start_redis)
    print('Configured Redis connections are ready.')
    if args.action == 'check':
        return
    base = [sys.executable, '-m', 'celery', '-A', args.app]
    # Existing responsive workers are reused, including workers started manually.
    probe = subprocess.run(base + ['inspect', 'ping', '--timeout=3'], capture_output=True)
    if probe.returncode == 0:
        print('A Celery worker is already responding; retaining it.')
    else:
        node = f'{args.app}-dev@{socket.gethostname()}'
        pid = state / 'worker.pid'
        if not live_pid(pid):
            pid.unlink(missing_ok=True)
            subprocess.run(base + ['worker', '--detach', '--loglevel=info', '--concurrency=2',
                                  '--hostname', node, '--queues',
                                  'default,media_queue,docs_queue,search_queue' if args.app == 'pwaninet' else 'celery',
                                  '--pidfile', str(pid),
                                  '--logfile', str(state / 'worker.log')], check=True)
        for _ in range(10):
            probe = subprocess.run(base + ['inspect', 'ping', '--destination', node, '--timeout=2'], capture_output=True)
            if probe.returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError(f'Celery worker did not respond; inspect {state / "worker.log"}.')
    pid = state / 'beat.pid'
    if live_pid(pid):
        print('Managed Celery Beat is already running; retaining it.')
    else:
        pid.unlink(missing_ok=True)
        subprocess.run(base + ['beat', '--detach', '--loglevel=info', '--pidfile', str(pid),
                              '--logfile', str(state / 'beat.log'),
                              '--schedule', str(state / 'beat-schedule')], check=True)
        for _ in range(20):
            if live_pid(pid):
                time.sleep(1)
                if live_pid(pid):
                    break
            time.sleep(.25)
        else:
            raise RuntimeError(f'Celery Beat did not stay running; inspect {state / "beat.log"}.')
    print(f'Celery is ready. Logs and PID files: {state}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
