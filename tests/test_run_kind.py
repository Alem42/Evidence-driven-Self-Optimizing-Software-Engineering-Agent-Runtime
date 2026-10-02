"""用途隔离与一次性迁移回归。 Purpose isolation and one-time migration regressions."""
import tempfile
import unittest
from pathlib import Path

from masa.domain.models import Budget, MasaError
from masa.infrastructure.store import Store
from masa.runtime.engine import Runtime
from masa.runtime.roles import RoleRuntime
from test_runtime import FakeExecutor


class RunKindTests(unittest.TestCase):
    def test_terminal_review_cannot_be_executed_as_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'go.mod').write_text('module demo\ngo 1.27.0\n')
            store=Store(root/'state');executor=FakeExecutor();runtime=Runtime(store,executor)
            try:
                rid=runtime.create(source,'review',Budget(),semantic_review={'status':'reviewed'})
                store.set_status(rid,'succeeded')
                self.assertEqual(store.run(rid)['data']['run_kind'],'semantic_review')
                with self.assertRaisesRegex(MasaError,'not authorized'):runtime.execute(rid)
                self.assertEqual(executor.calls,0)
            finally:store.close()

    def test_legacy_role_migration_does_not_rescan_after_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp))
            try:
                RoleRuntime(store)
                # 迁移标记保留时禁止读取旧表；模拟旧表清理后再次初始化。
                # The marker prevents reading legacy rows again, even after retiring the old table.
                store.db.execute('DROP TABLE role_calls');store.db.commit()
                RoleRuntime(store)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],1)
            finally:store.close()
