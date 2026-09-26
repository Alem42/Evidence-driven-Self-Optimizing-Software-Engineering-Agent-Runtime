"""补丁行为与崩溃恢复验收。 Patch behavior and crash-recovery acceptance tests."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from masa.adapters.sqlite import Store
from masa.domain import Budget, MasaError, digest
from masa.patching import Patches
from masa.runtime import Runtime
from masa.workspace import manifest
from test_runtime import FakeExecutor


class PowerLoss(BaseException):
    """模拟进程消失，绕过普通异常处理。 Simulate process loss outside normal error handling."""


class PatchTests(unittest.TestCase):
    def setUp(self):
        """准备独立双文件源仓库。 Prepare an isolated two-file source repository."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'go.mod').write_text('module demo\ngo 1.27.0\n')
        for name in ('one.go', 'two.go', 'one_test.go'):
            (self.source / name).write_bytes(b'package demo\n')
        self.original = manifest(self.source)
        self.store = Store(self.root / 'state')
        self.executor = FakeExecutor()
        self.runtime = Runtime(self.store, self.executor)
        self.rid = self.runtime.create(self.source, 'repair', Budget())
        self.workspace = Path(self.store.run(self.rid)['data']['workspace'])
        self.patches = Patches(self.store)
        self.body = {'base_snapshot':digest(self.original), 'files':[
            {'path':name, 'before_sha256':self.original[name], 'content':'package demo\n// repaired\n'}
            for name in ('one.go', 'two.go')]}

    def tearDown(self):
        """关闭连接并清理测试副本。 Close storage and clean test copies."""
        self.store.close()
        self.temp.cleanup()

    def reopen(self):
        """重新打开状态模拟重启。 Reopen persistent state to simulate restart."""
        self.store.close()
        self.store = Store(self.root / 'state')
        self.runtime = Runtime(self.store, self.executor)
        self.patches = Patches(self.store)

    def interrupt_after(self, count):
        """在指定文件替换后模拟断电。 Simulate power loss after a chosen replacement."""
        original = self.patches._replace
        def replace(*args):
            """执行替换并在目标边界中断。 Replace a file and interrupt at the chosen boundary."""
            original(*args)
            if args[2] + 1 == count:
                raise PowerLoss()
        with patch.object(self.patches, '_replace', side_effect=replace), self.assertRaises(PowerLoss):
            self.patches.apply(self.rid, self.body)

    def test_apply_idempotent_and_verify_new_snapshot(self):
        """重复请求不重复写入或扣费。 Repeated requests neither rewrite nor recharge."""
        result = self.patches.apply(self.rid, self.body)
        self.assertEqual(result, self.patches.apply(self.rid, self.body))
        self.assertEqual(self.store.run(self.rid)['tool_calls'], 1)
        self.assertNotEqual(result['snapshot_id'], result['before_snapshot'])
        run = self.runtime.execute(self.rid)
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(run['tool_calls'], 2)
        self.assertEqual(manifest(self.source), self.original)
        self.assertEqual(self.store.read(self.store.tools(self.rid)[0]['result_ref'])['snapshot_id'], result['snapshot_id'])

    def test_partial_write_restarts_without_rewriting_completed_file(self):
        """部分写入后只补写旧哈希文件。 After a partial write, only old-hash files are replaced."""
        self.interrupt_after(1)
        self.reopen()
        original = Patches._replace
        calls = []
        def observe(obj, workspace, change, index, run_id):
            """记录恢复期间实际写入的文件。 Record actual writes during reconciliation."""
            calls.append(change['path'])
            return original(obj, workspace, change, index, run_id)
        with patch.object(Patches, '_replace', observe):
            self.assertEqual(self.runtime.execute(self.rid)['status'], 'succeeded')
        self.assertEqual(calls, ['two.go'])
        self.assertEqual(manifest(self.source), self.original)

    def test_all_files_written_before_commit_recovers_without_writes(self):
        """已落盘但未提交时仅补账。 Reconcile a complete postimage by committing the ledger only."""
        self.interrupt_after(2)
        self.reopen()
        with patch.object(Patches, '_replace', side_effect=AssertionError('unexpected replay')):
            self.assertEqual(self.runtime.execute(self.rid)['status'], 'succeeded')

    def test_conflict_stops_before_any_remaining_write(self):
        """未知内容触发人工介入，不覆盖其他文件。 Unknown bytes stop all remaining writes."""
        self.interrupt_after(1)
        (self.workspace / 'one.go').write_text('unexpected external edit')
        self.reopen()
        run = self.runtime.execute(self.rid)
        self.assertEqual(run['status'], 'needs_attention')
        self.assertEqual((self.workspace / 'two.go').read_bytes(), b'package demo\n')
        self.assertEqual(self.executor.calls, 0)

    def test_reject_stale_paths_tests_and_duplicates_before_writing(self):
        """预检拒绝所有越界及陈旧输入。 Preflight rejects out-of-scope and stale requests."""
        for path in ('../one.go', '/one.go', 'one_test.go', 'go.mod', 'new.go', 'a/../one.go', 'one.go:stream'):
            body = {**self.body, 'files':[{**self.body['files'][0], 'path':path}]}
            with self.subTest(path=path), self.assertRaises(MasaError):
                self.patches.apply(self.rid, body)
        with self.assertRaises(MasaError):
            self.patches.apply(self.rid, {**self.body, 'base_snapshot':'0'*64})
        with self.assertRaises(MasaError):
            self.patches.apply(self.rid, {**self.body, 'files':[self.body['files'][0]]*2})
        with self.assertRaises(MasaError):
            self.patches.apply(self.rid, {**self.body, 'files':[{**self.body['files'][0], 'before_sha256':'0'*64}]})
        self.assertEqual(manifest(self.workspace), self.original)
        self.assertEqual(self.store.run(self.rid)['tool_calls'], 0)

    def test_completed_evidence_cannot_be_reused_after_patch(self):
        """有执行证据后禁止修改该 run。 Reject writes after a run has execution evidence."""
        self.runtime.execute(self.rid, pause_after=1)
        with self.assertRaisesRegex(MasaError, 'without attempts'):
            self.patches.apply(self.rid, self.body)

    def test_intent_before_first_write_recovers_and_budget_is_not_reset(self):
        """写前崩溃恢复不重复扣补丁预算。 Pre-write recovery does not recharge patch budget."""
        with patch.object(self.patches, '_replace', side_effect=PowerLoss), self.assertRaises(PowerLoss):
            self.patches.apply(self.rid, self.body)
        self.reopen()
        run = self.runtime.execute(self.rid)
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(run['tool_calls'], 2)
