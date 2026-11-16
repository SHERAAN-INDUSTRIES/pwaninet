"""Actual PostgreSQL transaction probe using independent libpq connections."""
import threading
import psycopg2


def verify(config):
    def connection():
        return psycopg2.connect(dbname=config['database'], user=config['user'],
            password=config['password'], host=config['host'], port=config['port'], connect_timeout=5)
    with connection() as a, connection() as b:
        with a.cursor() as c:
            c.execute('SELECT pg_backend_pid()'); pid_a = c.fetchone()[0]
            c.execute('CREATE TABLE wp0_probe (id integer PRIMARY KEY, value integer NOT NULL)')
            c.execute('INSERT INTO wp0_probe VALUES (1, 0)')
        a.commit()
        with b.cursor() as c:
            c.execute('SELECT pg_backend_pid()'); assert c.fetchone()[0] != pid_a
        b.rollback()
        locked = threading.Event(); release = threading.Event(); failures = []
        def holder():
            try:
                with a.cursor() as c:
                    c.execute('SELECT value FROM wp0_probe WHERE id=1 FOR UPDATE')
                    locked.set()
                    if not release.wait(5): raise RuntimeError('Probe barrier timeout')
                    c.execute('UPDATE wp0_probe SET value=1 WHERE id=1')
                a.commit()
            except BaseException as exc:
                failures.append(exc); locked.set(); a.rollback()
        t = threading.Thread(target=holder); t.start()
        try:
            assert locked.wait(5), 'Holder never acquired lock'
            if failures: raise failures[0]
            with b.cursor() as c:
                c.execute("SET LOCAL lock_timeout='300ms'")
                try:
                    c.execute('SELECT value FROM wp0_probe WHERE id=1 FOR UPDATE')
                except psycopg2.errors.LockNotAvailable:
                    b.rollback()
                else:
                    raise AssertionError('Independent connection unexpectedly acquired held row')
        finally:
            release.set(); t.join(6)
        assert not t.is_alive() and not failures, failures
        with b.cursor() as c:
            c.execute('SELECT value FROM wp0_probe'); assert c.fetchone()[0] == 1
            c.execute('UPDATE wp0_probe SET value=9')
        b.rollback()
        with b.cursor() as c:
            c.execute('SELECT value FROM wp0_probe'); assert c.fetchone()[0] == 1
            c.execute('DROP TABLE wp0_probe')
        b.commit()
    print('PASS independent connections, conflicting row lock, commit visibility, rollback')
