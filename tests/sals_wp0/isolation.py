"""Process-scoped guards installed before Django setup; used only by WP0 runners."""
import socket
import psycopg2
from psycopg2.extensions import parse_dsn, make_dsn
from contextlib import ExitStack, contextmanager
from unittest.mock import patch


@contextmanager
def guards(port):
    original_connect = socket.socket.connect
    original_pg = psycopg2.connect
    def pg_connect(dsn=None, **kwargs):
        extras = {k: kwargs[k] for k in ('connection_factory', 'cursor_factory', 'async_', 'async') if k in kwargs}
        options = {k: v for k, v in kwargs.items() if k not in extras}
        params = parse_dsn(make_dsn(dsn, **options))
        if (params.get('host') != '127.0.0.1' or params.get('port') != str(port)
                or params.get('user') != 'sals_wp0'
                or params.get('dbname') not in {'sals_wp0', 'test_sals_wp0', 'postgres'}
                or any(k in params for k in ('service', 'hostaddr', 'passfile'))):
            raise RuntimeError('WP0 blocked non-disposable libpq connection')
        return original_pg(**params, **extras)
    def connect(sock, address):
        if not (isinstance(address, tuple) and address[:2] == ('127.0.0.1', port)):
            raise RuntimeError('WP0 blocked external socket')
        return original_connect(sock, address)
    with ExitStack() as stack:
        stack.enter_context(patch('psycopg2.connect', pg_connect))
        stack.enter_context(patch('socket.socket.connect', connect))
        stack.enter_context(patch('socket.socket.connect_ex', side_effect=RuntimeError('WP0 blocked connect_ex')))
        stack.enter_context(patch('socket.socket.sendto', side_effect=RuntimeError('WP0 blocked datagram')))
        stack.enter_context(patch('requests.sessions.Session.request', side_effect=RuntimeError('WP0 blocked HTTP')))
        stack.enter_context(patch('celery.app.task.Task.apply_async', side_effect=RuntimeError('WP0 blocked task dispatch')))
        stack.enter_context(patch('celery.app.base.Celery.send_task', side_effect=RuntimeError('WP0 blocked send_task')))
        stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
        yield
