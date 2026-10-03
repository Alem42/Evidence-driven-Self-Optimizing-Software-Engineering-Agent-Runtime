"""角色响应与业务取消/期限的边界。 Role response boundaries for cancellation and deadlines."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from masa.application.planning import ProjectPlanning
from masa.domain.models import Budget, MasaError
from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.store import Store
from masa.runtime.engine import Runtime
from masa.runtime.roles import RoleRuntime
from test_auto_project import CompositeProvider
from test_project_plan import SPEC
from test_runtime import FakeExecutor


class RoleCancellationTests(unittest.TestCase):
    def create_run(self, root, store):
        """创建仅用于隔离测试的真实账本。 Create a real ledger in an isolated test directory."""
        source=root/'source'
        source.mkdir(exist_ok=True)
        (source/'go.mod').write_text('module demo\n\ngo 1.27.0\n',encoding='utf-8')
        (source/'main.go').write_text('package demo\n',encoding='utf-8')
        return Runtime(store,FakeExecutor()).create(source,'Build CLI',Budget())

    def test_cancel_during_planner_preserves_response_without_review_or_tester(self):
        """另一连接取消阻塞中的 Planner：输出保留，但不发布待审方案或调用 Tester。 Keep a received response without applying it after concurrent cancellation."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);entered=threading.Event();release=threading.Event()
            ids=[];errors=[];purposes=[]

            class BlockingProvider(CompositeProvider):
                def respond(self,context):
                    purposes.append(context['purpose'])
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('test did not release the provider')
                    return super().respond(context)

            def worker():
                store=Store(root)
                try:
                    ProjectPlanning(store,FakeExecutor()).generate(BlockingProvider(),'Build CLI',ids.append)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    store.close()

            thread=threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(5),'Planner never reached the blocking provider')
                control=Store(root)
                try:
                    control.cancel(ids[0])
                finally:
                    control.close()
            finally:
                release.set();thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(purposes,['project_planner'])
            self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],MasaError)
            store=Store(root)
            try:
                run=store.run(ids[0])
                self.assertEqual(run['status'],'cancelled')
                self.assertEqual(run['data']['project_plan']['status'],'cancelled')
                self.assertEqual(run['model_calls'],1)
                row=RoleRuntime(store).states(ids[0])[0]
                self.assertEqual(row['status'],'completed')
                self.assertEqual(store.read(row['output_ref']),SPEC)
                self.assertNotIn('project_review_requested',[e['type'] for e in store.events(ids[0])])
            finally:
                store.close()

    def test_real_provider_timeout_is_minimum_of_model_limit_and_run_remaining(self):
        """真实传输获得剩余期限，既不超过模型限制也不超过任务限制。 Give real transports the minimum of model timeout and run time remaining."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Store(root)
            try:
                for configured,expected in [(600,7),(5,5)]:
                    with self.subTest(configured=configured):
                        rid=self.create_run(root,store)
                        with store.transaction():
                            store.save_metadata(rid,'deadline_at',1007,'deadline_adjusted',payload={'deadline_at':1007})
                        provider=ChatProvider({'model_type':'local','base_url':'http://127.0.0.1:11434',
                                               'model':'test:latest','timeout_seconds':configured})
                        with patch('masa.runtime.roles.time.time',return_value=1000), \
                             patch.object(provider,'respond',return_value=SPEC) as respond:
                            self.assertEqual(RoleRuntime(store).call(rid,provider,'project_planner',{}),SPEC)
                            self.assertEqual(respond.call_args.kwargs,{'timeout':expected})
            finally:
                store.close()

    def test_deadline_after_received_response_preserves_output_and_fails_business_state(self):
        """响应返回时已过期，保留调用成功事实但不发布业务成功。 Preserve response facts while refusing an expired business result."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Store(root);ids=[]

            class ExpiringProvider(CompositeProvider):
                def respond(self,context):
                    with store.transaction():
                        store.save_metadata(ids[0],'deadline_at',time.time()-1,'deadline_adjusted',
                                            payload={'reason':'test expiry'})
                    return super().respond(context)

            try:
                with self.assertRaisesRegex(MasaError,'deadline expired'):
                    ProjectPlanning(store,FakeExecutor()).generate(ExpiringProvider(),'Build CLI',ids.append)
                run=store.run(ids[0])
                self.assertEqual(run['status'],'failed')
                self.assertEqual(run['data']['project_plan']['status'],'failed')
                self.assertEqual(run['data']['project_plan']['error'],'project deadline expired')
                row=RoleRuntime(store).states(ids[0])[0]
                self.assertEqual(row['status'],'completed')
                self.assertEqual(store.read(row['output_ref']),SPEC)
                self.assertEqual(run['model_calls'],1)
            finally:
                store.close()

    def test_late_publication_and_failure_cannot_overwrite_cancelled_state(self):
        """取消发生在响应与发布之间时，更新与失败处理均保持 cancelled。 Keep cancellation terminal across late publication and failure handling."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Store(root)
            try:
                rid=self.create_run(root,store)
                store.cancel(rid)
                planning=ProjectPlanning(store,FakeExecutor())
                for status,plan in [('paused',{'status':'awaiting_review'}),('failed',{'status':'failed','error':'late error'})]:
                    planning.update(rid,plan,status,'late_publication')
                    self.assertEqual(store.run(rid)['status'],'cancelled')
                    self.assertEqual(store.run(rid)['data']['project_plan']['status'],'cancelled')
                    self.assertEqual(plan['status'],'cancelled')
            finally:
                store.close()

    def test_pre_cancelled_call_never_sends_or_spends_model_budget(self):
        """请求前取消不发送模型调用，也不消耗调用预算。 Reject pre-cancelled work before calling or charging the model."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Store(root)
            try:
                rid=self.create_run(root,store);store.cancel(rid)
                provider=CompositeProvider()
                with patch.object(provider,'respond') as respond:
                    with self.assertRaisesRegex(MasaError,'cancelled'):
                        RoleRuntime(store).call(rid,provider,'project_planner',{})
                    respond.assert_not_called()
                self.assertEqual(store.run(rid)['model_calls'],0)
            finally:
                store.close()
