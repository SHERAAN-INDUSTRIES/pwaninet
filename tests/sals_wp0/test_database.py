"""Requires WP0's disposable PostgreSQL; never run against a shared database."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DatabaseFoundationTests(TransactionTestCase):
    def test_vector_and_transactional_ddl_rollback(self):
        with connection.cursor() as c:
            c.execute("SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector")
            self.assertEqual(c.fetchone()[0], 0)
        from django.db import transaction
        with self.assertRaisesRegex(RuntimeError, 'intentional rollback'):
            with transaction.atomic():
                with connection.cursor() as c:
                    c.execute('CREATE TABLE wp0_rolled_back (id integer)')
                raise RuntimeError('intentional rollback')
        with connection.cursor() as c:
            c.execute("SELECT to_regclass('wp0_rolled_back')")
            self.assertIsNone(c.fetchone()[0])

    def test_latest_spark_migration_backward_and_forward(self):
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes('pwanimate')
        self.assertEqual(len(leaves), 1)
        leaf = leaves[0]
        parents = [key for key in executor.loader.disk_migrations[leaf].dependencies if key[0] == 'pwanimate']
        self.assertEqual(len(parents), 1)
        try:
            executor.migrate(parents)
        finally:
            MigrationExecutor(connection).migrate([leaf])
