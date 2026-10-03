"""任务报告：用量、耗时和工具调用来自真实事件，缺失用量保持未知。 Usage report from real events; missing usage stays unknown."""
import tempfile
import unittest
from pathlib import Path

from masa.application.planning import ProjectPlanning
from masa.application.usage import task_report
from masa.infrastructure.store import Store
from test_project_plan import PlannerProvider
from test_runtime import FakeExecutor


class UsageReportTests(unittest.TestCase):
    def test_report_pairs_calls_and_keeps_unknown_usage_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                rid = ProjectPlanning(store, FakeExecutor()).generate(PlannerProvider(), 'Build a CLI')
                report = task_report(store, rid)
                self.assertEqual(report['outcome'], 'waiting')
                self.assertEqual(report['project_id'], rid)
                self.assertGreaterEqual(report['totals']['calls'], 2)
                self.assertEqual({c['step_id'] for c in report['calls']} >= {'project_planner', 'project_tester'}, True)
                # 汇总必须等于逐次调用之和；缺用量的调用不计入也不被当成 0。
                known = [c['total_tokens'] for c in report['calls'] if c['total_tokens'] is not None]
                self.assertEqual(report['totals']['total_tokens'], sum(known))
                self.assertEqual(report['totals']['unknown_usage_calls'], sum(c['total_tokens'] is None for c in report['calls']))
                self.assertEqual(sum(m['calls'] for m in report['by_model']), report['totals']['calls'])
            finally:
                store.close()

    def test_failed_and_unfinished_calls_are_marked(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                rid = ProjectPlanning(store, FakeExecutor()).generate(PlannerProvider(), 'Build a CLI')
                route = {'model': 'm', 'provider': 'openai-compatible', 'base_url': 'https://api.example.com'}
                store._event(rid, 'model_requested', {'step_id': 'x', 'invocation_id': 'a', 'attempt_no': 1, 'route': route})
                store._event(rid, 'model_requested', {'step_id': 'x', 'invocation_id': 'b', 'attempt_no': 2, 'route': route})
                store._event(rid, 'model_failed', {'step_id': 'x', 'invocation_id': 'a', 'attempt_no': 1})
                store.db.commit()
                report = task_report(store, rid)
                mine = [c for c in report['calls'] if c['step_id'] == 'x']
                self.assertEqual({c['status'] for c in mine}, {'failed', 'pending'})
                self.assertTrue(all(c['kind'] == 'cloud' and c['total_tokens'] is None for c in mine))
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()


class PreflightTests(unittest.TestCase):
    """确定性预检：第三方依赖、同目录包名冲突、根目录 Go 文件。 Deterministic Go preflight."""

    def test_preflight_rejects_third_party_imports_and_mixed_packages(self):
        from masa.domain.models import MasaError
        from masa.domain.proposals import preflight_go, validate_spec
        ok = {'a/a.go': 'package a\n\nimport (\n\t"fmt"\n\t"example.com/m/b"\n)\n', 'a/a_test.go': 'package a_test\n'}
        preflight_go(ok, 'example.com/m')
        with self.assertRaisesRegex(MasaError, 'third-party'):
            preflight_go({'a/a.go': 'package a\n\nimport "golang.org/x/exp/constraints"\n'}, 'example.com/m')
        with self.assertRaisesRegex(MasaError, 'mixes packages'):
            preflight_go({'pkg/core/g.go': 'package core\n', 'pkg/core/g_test.go': 'package main\n'}, 'example.com/m')
        spec = {'summary': 's', 'module': 'example.com/m', 'entrypoint': 'cmd/app/main.go', 'acceptance': ['x'],
                'files': [{'path': p, 'purpose': 'p'} for p in ('go.mod', 'cmd/app/main.go', 'interval.go', 'interval_test.go')]}
        with self.assertRaisesRegex(MasaError, 'subdirectories'):
            validate_spec(spec)
