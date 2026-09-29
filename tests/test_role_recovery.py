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
    def test_automatic_job_reopens_and_continues_after_plan_approval(self):
        """后台任务重启后从已批准规划继续，不重复规划。 Resume the full automatic job from an approved plan checkpoint."""
        from masa.application.console import Console
        from masa.infrastructure.jobs import Jobs
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Store(root);provider=CompositeProvider()
            planner=ProjectPlanning(store,FakeExecutor())
            plan=planner.generate(provider,'Build CLI');meta=store.run(plan)['data']['project_plan']
            planner.approve(plan,{'spec_ref':meta['spec_ref'],'checks_ref':meta['checks_ref'],
                'spec':store.read(meta['spec_ref']),'checks':store.read(meta['checks_ref'])})
            Jobs(root)['saved-job']={'status':'running','mode':'auto','phase':'generation',
                'plan_id':plan,'run_id':plan,'attempt':0,'started':1,'request':{'goal':'Build CLI','api_profile_id':None}}
            store.close()
            console=Console(root,'unused','unused',Path.cwd())
            console.settings.provider=lambda *_:provider
            self.assertEqual(console.project_job('saved-job')['status'],'interrupted')
            with patch('masa.application.console.Runner',lambda *args:FakeExecutor()),patch.object(provider,'respond',wraps=provider.respond) as calls:
                console.start_autonomous_project_job({},resume_job='saved-job')
                console.job_thread.join(timeout=10)
                self.assertEqual(calls.call_count,1)
                self.assertEqual(calls.call_args.args[0]['purpose'],'project_developer')
            self.assertEqual(console.project_job('saved-job')['status'],'completed')
            self.assertEqual(Jobs(root)['saved-job']['status'],'completed')
            console.close()

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

    def test_auto_resumes_saved_repair_and_published_generation(self):
        """覆盖修复与生成发布后的检查点窗口。 Cover saved repair and post-publication checkpoint gaps."""
        from masa.application.console import Console
        from masa.infrastructure.jobs import Jobs
        from masa.runtime.engine import Runtime
        from test_project_generation import FILES
        for phase in ('repair','generation','unknown_repair'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);store=Store(root);provider=CompositeProvider()
                p=ProjectPlanning(store,FakeExecutor());plan=p.generate(provider,'Build CLI')
                meta=store.run(plan)['data']['project_plan']
                p.approve(plan,{'spec_ref':meta['spec_ref'],'checks_ref':meta['checks_ref'],
                    'spec':store.read(meta['spec_ref']),'checks':store.read(meta['checks_ref'])})
                g=ProjectGeneration(store,FakeExecutor());draft=g.generate(plan,provider)
                resume=draft;attempt=0
                if phase in {'repair','unknown_repair'}:
                    meta=store.run(draft)['data']['project_plan']
                    failed=g.approve(draft,{'files_ref':meta['files_ref'],'files':FILES})
                    Runtime(store,FakeExecutor(exit_code=1)).execute(failed)
                    ids=[]
                    with patch.object(provider,'respond',side_effect=Crash if phase=='unknown_repair' else None,return_value={'internal/app/app.go':'package app\n\nfunc Value() int { return 43 }\n'}),patch.object(g.planning,'update',side_effect=Crash):
                        with self.assertRaises(Crash):g.repair(failed,provider,on_created=ids.append)
                    resume=ids[0];attempt=1
                Jobs(root)['recovery']={'status':'running','mode':'auto','phase':'repair' if phase=='unknown_repair' else phase,
                    'plan_id':plan,'run_id':resume,'attempt':attempt,'started':1,
                    'provider':provider.profile,'request':{'goal':'Build CLI','api_profile_id':None}}
                store.close();console=Console(root,'unused','unused',Path.cwd())
                console.settings.provider=lambda *_:provider
                with patch('masa.application.console.Runner',lambda *args:FakeExecutor()),patch.object(provider,'respond',side_effect=AssertionError('unexpected replay')):
                    console.start_autonomous_project_job({},resume_job='recovery')
                    console.job_thread.join(timeout=10)
                job=console.project_job('recovery')
                if phase=='unknown_repair':
                    self.assertEqual(job['status'],'failed',job)
                    self.assertIn('result unavailable',job['error'])
                    self.assertEqual(console.detail(resume)['run']['model_calls'],1)
                    console.close()
                    continue
                self.assertEqual(job['status'],'completed',job)
                self.assertEqual(job['attempt'],attempt)
                self.assertEqual(console.detail(job['result']['id'])['run']['status'],'succeeded')
                self.assertFalse(console.detail(resume)['role_active'])
                console.close()
