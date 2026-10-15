"""经验库（最小版）：签名归一化、只存有证据的做法、容量与按效用淘汰、命中才取；以及接入修复流程（默认关闭、级别 1 只记录、级别 2 注入、关闭时与原来一致）。
Experience library (minimal): signature normalisation, evidence-only remedies, capacity and utility eviction, read-on-hit; plus the repair-flow hook (off by default, level 1 records, level 2 injects, unchanged when off)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.orchestration.routing import validate_policy
from masa.domain.models import MasaError
from masa.intelligence.library import Library, signature

from test_fix_flow import FixFlowCase, Model, World


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class SignatureTests(unittest.TestCase):
    def test_equal_mistakes_in_different_tasks_share_a_signature(self):
        a = signature('implementation:compile', './internal/app/app.go:47:17: undefined: strings.Foo')
        b = signature('implementation:compile', 'cmd/csv/main.go:9:3: undefined: strings.Foo')
        self.assertEqual(a, b)
        self.assertNotEqual(a, signature('test:compile', './internal/app/app.go:47:17: undefined: strings.Foo'))  # 类别也进签名 / the class is part of it
        self.assertEqual(signature('x', 'count("你好\n") = 14, want 13'), signature('x', 'count("abc") = 9, want 8'))  # 引号内容与数字被抹掉 / quoted text and numbers are erased
        self.assertNotEqual(signature('x', 'syntax error: unexpected name'), signature('x', 'imported and not used'))


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.path = Path(self.temp.name) / 'lib.sqlite3'

    def library(self, **kwargs):
        lib = Library(self.path, clock=self.clock, **kwargs)
        self.addCleanup(lib.close)
        return lib

    def test_only_a_verified_success_makes_a_remedy_available(self):
        lib = self.library()
        sig = lib.record_failure('test:assertion', 'got 3, want 4')
        self.assertIsNone(lib.lookup('test:assertion', 'got 3, want 4'))  # 只见过失败：没有做法 / only a failure seen: no remedy
        lib.record_remedy(sig, 'revise: changed app_test.go', 'run1', success=False)
        self.assertIsNone(lib.lookup('test:assertion', 'got 5, want 6'))  # 失败的修复不加分 / a failed fix earns nothing
        lib.record_remedy(sig, 'revise: changed app_test.go', 'run2', success=True)
        hit = lib.lookup('test:assertion', 'got 5, want 6')  # 归一化后同一条 / the same entry after normalisation
        self.assertEqual((hit['remedy'], hit['successes'], hit['hits']), ('revise: changed app_test.go', 1, 1))

    def test_repeated_failures_raise_hits_not_entries(self):
        lib = self.library()
        for _ in range(4):
            lib.record_failure('implementation:compile', 'undefined: strings.Foo')
        stats = lib.stats()
        self.assertEqual((stats['entries'], stats['top'][0]['hits']), (1, 4))

    def test_capacity_evicts_the_lowest_utility_first(self):
        lib = self.library(max_entries=3)
        useful = lib.record_failure('a:k', 'useful mistake')
        for _ in range(3):
            lib.record_failure('a:k', 'useful mistake')
        lib.record_remedy(useful, 'fix it', 'r', True)
        one_off = [lib.record_failure('a:k', f'rare mistake number {chr(97 + i)}') for i in range(5)]
        self.assertEqual(lib.stats()['entries'], 3)
        self.assertIsNotNone(lib.lookup('a:k', 'useful mistake'))  # 有成功记录且命中多的留下 / the proven, frequently hit entry stays
        remaining = {t['signature'] for t in lib.stats()['top']}
        self.assertTrue(remaining & {useful})
        self.assertGreater(len(set(one_off) - remaining), 0)

    def test_byte_capacity_and_recency(self):
        lib = self.library(capacity_bytes=600)
        old = lib.record_failure('a:k', 'old mistake')
        lib.record_remedy(old, 'x' * 300, 'r', True)
        self.clock.now += 90 * 86400  # 90 天过去 / ninety days pass
        new = lib.record_failure('a:k', 'new mistake')
        lib.record_remedy(new, 'y' * 300, 'r', True)
        self.assertLessEqual(lib.stats()['bytes'], 600)
        self.assertIsNone(lib.lookup('a:k', 'old mistake'))  # 太久没用的先走 / the stale one goes first
        self.assertIsNotNone(lib.lookup('a:k', 'new mistake'))

    def test_the_policy_level_is_bounded(self):
        self.assertEqual(validate_policy({})['library'], 0)
        for bad in (-1, 3, True, 'x'):
            with self.assertRaises(MasaError):
                validate_policy({'library': bad})


class FlowHookTests(FixFlowCase):
    """经验库已停用：无论策略 library 设成多少，流程里都不记录、不注入、不创建文件。 The library is switched off: whatever policy `library` says, the flow records, injects and creates nothing."""

    def run_with(self, library, path):
        world = World()
        local, cloud = Model('small', 'local', world), Model('big', 'cloud', world)
        with patch.dict(os.environ, {'MASA_LIBRARY': str(path)}):
            store, job = self.run_task(local, cloud, world, policy={'library': library})
        return store, job, local

    def test_the_hooks_are_inert_at_every_level(self):
        with tempfile.TemporaryDirectory() as temp:
            for level in (0, 1, 2):
                path = Path(temp) / f'lib{level}.sqlite3'
                store, job, local = self.run_with(level, path)
                self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
                self.assertFalse(path.exists())
                self.assertEqual(self.events(store, 'library_hit'), [])
                repair = next(c for c in local.contexts if c['purpose'] == 'project_repair')
                self.assertNotIn('经验库', repair['feedback'])


if __name__ == '__main__':
    unittest.main()
