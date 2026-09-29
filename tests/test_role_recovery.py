"""真实中断边界的持久角色恢复测试。 Durable role recovery at interruption boundaries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from masa.infrastructure.store import Store
from masa.runtime.roles import RoleRuntime
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.domain.models import MasaError
from test_auto_project import CompositeProvider
from test_runtime import FakeExecutor


class Crash(BaseException):
    pass


class RoleRecoveryTests(unittest.TestCase):
    def test_saved_planner_response_survives_reopen_before_publication(self):
        """响应落盘后发布前崩溃，只补 Tester 调用。 Reopening after response commit does not charge Planner twice."""
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));service=ProjectPlanning(store,FakeExecutor());ids=[]
            with patch.object(service,'update',side_effect=Crash):
                with self.assertRaises(Crash):service.generate(CompositeProvider(),'Build CLI',ids.append)
            rid=ids[0];self.assertEqual(store.run(rid)['model_calls'],1)
            store.close();store=Store(Path(temp))
            provider=CompositeProvider()
            with patch.object(provider,'respond',wraps=provider.respond) as respond:
                ProjectPlanning(store,FakeExecutor()).generate(provider,'Build CLI',resume_id=rid)
                self.assertEqual(respond.call_count,1)
                self.assertEqual(respond.call_args.args[0]['purpose'],'project_tester')
            self.assertEqual(store.run(rid)['data']['project_plan']['status'],'awaiting_review')
            self.assertEqual(store.run(rid)['model_calls'],2)
            store.close()

    def test_unknown_call_is_not_replayed(self):
        """网络请求中断后未知结果不得隐式重试。 An interrupted network call must not be replayed silently."""
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));ids=[];provider=CompositeProvider()
            with patch.object(provider,'respond',side_effect=Crash):
                with self.assertRaises(Crash):ProjectPlanning(store,FakeExecutor()).generate(provider,'Build CLI',ids.append)
            rid=ids[0];store.close();store=Store(Path(temp))
            with patch.object(provider,'respond') as respond:
                with self.assertRaisesRegex(MasaError,'result unavailable'):
                    ProjectPlanning(store,FakeExecutor()).generate(provider,'Build CLI',resume_id=rid)
                respond.assert_not_called()
            self.assertEqual(store.run(rid)['model_calls'],1)
            store.close()

    def test_developer_output_recovers_without_model_or_approval(self):
        """恢复生成只发布待审草稿，不自动批准代码。 Restore a saved draft without approving or recalling the model."""
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));p=ProjectPlanning(store,FakeExecutor());provider=CompositeProvider()
            parent=p.generate(provider,'Build CLI');meta=store.run(parent)['data']['project_plan']
            p.approve(parent,{'spec_ref':meta['spec_ref'],'checks_ref':meta['checks_ref'],
                'spec':store.read(meta['spec_ref']),'checks':store.read(meta['checks_ref'])})
            g=ProjectGeneration(store,FakeExecutor());ids=[]
            with patch.object(g.planning,'update',side_effect=Crash):
                with self.assertRaises(Crash):g.generate(parent,provider,ids.append)
            store.close();store=Store(Path(temp))
            with patch.object(provider,'respond') as respond:
                ProjectGeneration(store,FakeExecutor()).generate(parent,provider,resume_id=ids[0])
                respond.assert_not_called()
            self.assertEqual(store.run(ids[0])['data']['project_plan']['status'],'awaiting_review')
            self.assertEqual(store.run(ids[0])['model_calls'],1)
            store.close()
