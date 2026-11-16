"""Infrastructure-only unit tests: no Django setup, DB or provider calls required."""
import socket
import unittest
from unittest.mock import Mock, patch
import requests
from .isolation import guards


class IsolationTests(unittest.TestCase):
    def test_http_tasks_and_sockets_fail_closed(self):
        from celery import Celery
        app = Celery('wp0', broker='memory://')
        @app.task
        def sample():
            raise AssertionError('Task must never execute')
        with guards(65432):
            for action in [lambda: requests.get('https://example.invalid'),
                           lambda: sample.delay(), lambda: app.send_task('sample')]:
                with self.assertRaises(RuntimeError): action()
            with socket.socket() as s:
                with self.assertRaises(RuntimeError): s.connect(('127.0.0.1',5432))
                with self.assertRaises(RuntimeError): s.connect(('example.invalid',443))

    def test_only_reserved_endpoint_can_reach_socket_connect(self):
        with patch('socket.socket.connect') as original:
            with guards(65432), socket.socket() as s:
                s.connect(('127.0.0.1',65432))
                original.assert_called_once()

    def test_native_libpq_cannot_bypass_socket_guard(self):
        import psycopg2
        with guards(65432):
            for args in [dict(host='127.0.0.1', port=5432, dbname='production', user='postgres'),
                         dict(host='db.example.invalid', port=65432, dbname='sals_wp0', user='sals_wp0')]:
                with self.assertRaises(RuntimeError): psycopg2.connect(**args)

    def test_django_cursor_factory_is_preserved(self):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with patch('psycopg2.connect') as original:
            with guards(65432):
                psycopg2.connect(host='127.0.0.1', port=65432, dbname='sals_wp0',
                                 user='sals_wp0', cursor_factory=RealDictCursor)
            self.assertIs(original.call_args.kwargs['cursor_factory'], RealDictCursor)
