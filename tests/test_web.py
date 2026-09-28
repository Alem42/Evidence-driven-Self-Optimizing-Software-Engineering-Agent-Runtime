import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from masa.interfaces.http.server import make_server
from masa.application.console import Console
from masa.infrastructure.settings import Settings
from masa.infrastructure.store import Store
from test_runtime import FakeExecutor


class WebTests(unittest.TestCase):
    def test_results_expose_test_setup_repair_advice(self):
        """HTTP 修复建议来自实际工具输出，前端无需重复猜测。 Derive browser repair advice from recorded tool output."""
        from masa.runtime.engine import Runtime
        from masa.domain.models import Budget
        class FailedBuild(FakeExecutor):
            def execute(self,request,workspace,cancelled):
                result=super().execute(request,workspace,cancelled)
                result.update(exit_code=1,stdout='main_test.go:103: build failed\nno Go files in C:\\work\\cmd\n')
                return result
        store=Store(self.root/'state')
        try:
            runtime=Runtime(store,FailedBuild())
            rid=runtime.create(self.source,'verify CLI',Budget())
            runtime.execute(rid)
        finally:store.close()
        status,result=self.request('/api/runs/'+rid+'/results')
        self.assertEqual(status,200)
        self.assertEqual(result['repair_advice']['action'],'revise_tests')
        self.assertEqual(result['checks'][0]['result']['exit_code'],1)

    def test_second_service_cannot_listen_on_same_port(self):
        """同端口只允许一个工作台服务。 Prevent two workbench versions from sharing one port."""
        with self.assertRaises(OSError):
            make_server(self.console,self.server.server_port)

    def test_http_auto_project_finishes_with_gate_without_intermediate_clicks(self):
        """一次 HTTP 请求后可轮询到真实 Gate，审批来自自动模式。 One request advances to a real Gate without intermediate clicks."""
        from test_auto_project import CompositeProvider
        with patch.object(self.console.settings,'provider',return_value=CompositeProvider()), patch('masa.application.console.Runner',return_value=FakeExecutor()):
            status, started=self.request('/api/projects/plan',{'goal':'Build a CLI','auto_verify':True})
            self.assertEqual(status,200,started)
            self.console.job_thread.join(timeout=10)
        status,job=self.request('/api/jobs/'+started['job_id'])
        self.assertEqual(status,200)
        self.assertEqual(job['status'],'completed')
        status,view=self.request('/api/projects/'+job['result']['id'])
        self.assertEqual(status,200)
        self.assertEqual(view['stages'][-1]['role'],'gate')
        self.assertEqual(view['stages'][-1]['status'],'succeeded')
        _, detail=self.request('/api/runs/'+job['result']['id'])
        ref=detail['run']['data']['project_bundle']['approval_ref']
        status,artifact=self.request('/api/runs/'+job['result']['id']+'/artifacts/'+ref)
        self.assertEqual(status,200)
        self.assertIn('cmd/app/main.go',artifact['artifact']['files'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'repo'
        self.source.mkdir()
        (self.source / 'go.mod').write_text('module demo\ngo 1.27.0\n')
        self.console = Console(self.root / 'state', 'missing', 'missing', self.root)
        self.server = make_server(self.console, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, html = self.request('/')
        self.assertEqual(status, 200)
        self.token = re.search(r'name="masa-token" content="([^"]+)"', html).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.console.close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        values = {'Content-Type': 'application/json', 'X-MASA-Token': getattr(self, 'token', '')}
        values.update(headers or {})
        connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), values)
        response = connection.getresponse()
        status, raw = response.status, response.read().decode()
        connection.close()
        return status, json.loads(raw) if path.startswith('/api/') else raw

    def wait_run(self, rid):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, detail = self.request('/api/runs/' + rid)
            if not detail['active']:
                self.assertIsNone(detail['worker_error'])
                return detail
            time.sleep(.02)
        self.fail('worker did not finish')

    def test_rerun_preserves_snapshot_and_returns_real_ledger_outputs(self):
        with patch('masa.application.console.Runner', return_value=FakeExecutor()):
            _, created = self.request('/api/runs', {'repo': str(self.source), 'goal': 'verify'})
            original = self.wait_run(created['id'])
            status, result = self.request('/api/runs/' + created['id'] + '/rerun', {})
            self.assertEqual(status, 200, result)
            rerun = self.wait_run(result['id'])
            self.assertEqual(rerun['run']['status'], 'succeeded')
            self.assertEqual(rerun['run']['model_calls'], 0)
            self.assertEqual([n['type'] for n in rerun['run']['data']['graph']['nodes']], ['tool', 'tool', 'tool', 'gate'])
            self.assertEqual(rerun['run']['data']['parent_run_id'], created['id'])
            self.assertEqual(rerun['run']['data']['snapshot_id'], original['run']['data']['snapshot_id'])
            self.assertNotEqual(rerun['run']['data']['workspace'], original['run']['data']['workspace'])
            status, outputs = self.request('/api/runs/' + result['id'] + '/results')
            self.assertEqual(status, 200)
            self.assertEqual({c['operation'] for c in outputs['checks']}, {'go_test', 'go_vet', 'go_fmt_check'})
            self.assertTrue(all(c['result']['stdout'] == 'test evidence' for c in outputs['checks']))
            Path(original['run']['data']['workspace'], 'changed.go').write_text('package demo\n')
            self.assertEqual(self.request('/api/runs/' + created['id'] + '/rerun', {})[0], 400)

    def test_rerun_rejects_paused_run(self):
        with patch('masa.application.console.Runner', return_value=FakeExecutor()):
            _, created = self.request('/api/runs', {'repo': str(self.source), 'goal': 'verify', 'pause_after': True})
            self.wait_run(created['id'])
            self.assertEqual(self.request('/api/runs/' + created['id'] + '/rerun', {})[0], 400)

    def test_project_plan_http_review_and_execution_guards(self):
        from test_project_plan import PlannerProvider, SPEC, CHECKS
        with patch.object(self.console.settings, 'provider', return_value=PlannerProvider()), patch('masa.application.console.Runner', return_value=FakeExecutor()):
            status, result = self.request('/api/projects/plan', {'goal':'Build a CSV CLI'})
            self.assertEqual(status,200,result)
            rid=result['id']
            detail=self.request('/api/runs/'+rid)[1]
            plan=detail['run']['data']['project_plan']
            self.assertEqual(self.request('/api/runs/'+rid+'/artifacts/'+plan['spec_ref'])[1]['artifact'],SPEC)
            self.assertEqual(self.request('/api/runs/'+rid+'/resume',{})[0],400)
            body={'spec_ref':plan['spec_ref'],'checks_ref':plan['checks_ref'],'spec':SPEC,'checks':CHECKS}
            self.assertEqual(self.request('/api/runs/'+rid+'/approve-project',body)[0],200)
            self.assertEqual(self.request('/api/runs/'+rid+'/resume',{})[0],400)
            self.assertEqual(self.request('/api/runs/'+rid+'/rerun',{})[0],400)
            self.assertEqual(self.request('/api/runs/'+rid)[1]['tools'],[])
            from test_project_generation import DeveloperProvider, FILES
            with patch.object(self.console.settings,'provider',return_value=DeveloperProvider()):
                status,draft=self.request('/api/runs/'+rid+'/generate-project',{})
                self.assertEqual(status,200,draft)
                draft_id=draft['id']
                data=self.request('/api/runs/'+draft_id)[1]['run']['data']['project_plan']
                status,execution=self.request('/api/runs/'+draft_id+'/approve-project-code',{'files_ref':data['files_ref'],'files':FILES})
                self.assertEqual(status,200,execution)
                self.assertEqual(self.wait_run(execution['id'])['run']['status'],'succeeded')

    def test_background_planning_progress_and_logs(self):
        from test_project_plan import PlannerProvider
        with patch.object(self.console.settings,'provider',return_value=PlannerProvider()), patch('masa.application.console.Runner',return_value=FakeExecutor()):
            status,job=self.request('/api/projects/plan',{'goal':'A CLI','background':True})
            self.assertEqual(status,200)
            self.console.job_thread.join(10)
            status,progress=self.request('/api/jobs/'+job['job_id'])
            self.assertEqual(progress['status'],'completed',progress)
            self.assertEqual(progress['stage'],'project_tester')
            rid=progress['result']['id']
            self.assertIn('project_planner',self.request('/api/runs/'+rid+'/logs')[1]['text'])
            self.assertEqual(self.request('/api/runs/'+rid+'/open-workspace',{})[0],400)

    def test_local_session_and_static_access(self):
        for headers in ({'X-MASA-Token':''}, {'Origin':'https://evil.example'}, {'Host':'evil.example'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/runs', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/runs')[0], 200)
        self.assertEqual(self.request('/app.js')[0], 200)
        self.assertEqual(self.request('/style.css')[0], 200)
        self.assertEqual(self.request('/../provider.json')[0], 404)
        self.assertEqual(self.request('/api/runs', [1])[0], 400)

    def test_credentials_never_returned_or_persisted(self):
        secret = 'synthetic-test-secret'
        status, result = self.request('/api/settings', {'name':'demo','base_url':'https://example.com/v1','model':'future','api_key':secret})
        self.assertEqual(status, 200)
        self.assertTrue(result['key_configured'])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, self.console.settings.path.read_text())
        self.assertFalse(Settings(self.root / 'state').public()['key_configured'])
        self.assertFalse(result['execution_connected'])
        self.assertEqual(self.request('/api/settings', {'base_url':'https://user:secret@example.com'})[0], 400)
        self.assertFalse(self.request('/api/settings', {'clear_key':True})[1]['key_configured'])

    def test_explicit_local_credentials_survive_restart_and_clear(self):
        settings=self.console.settings
        settings.save({'base_url':'https://example.com','model':'test','api_key':'local-test-secret','persist_key':True})
        restored=Settings(self.root/'state')
        self.assertTrue(restored.public()['key_configured'])
        self.assertNotIn('local-test-secret',json.dumps(restored.public()))
        restored.save({'clear_key':True})
        self.assertFalse(Settings(self.root/'state').public()['key_configured'])

    def test_run_pause_resume_revision_and_evidence(self):
        executor = FakeExecutor()
        with patch('masa.application.console.Runner', return_value=executor):
            body = {'repo':str(self.source),'goal':'verify','pause_after':True}
            status, result = self.request('/api/runs', body)
            self.assertEqual(status, 200)
            rid = result['id']
            detail = self.wait_run(rid)
            self.assertEqual(detail['run']['status'], 'paused')
            ref = detail['tools'][0]['result_ref']
            self.assertEqual(self.request('/api/runs/'+rid+'/artifacts/'+ref)[0], 200)
            self.assertEqual(self.request('/api/runs/'+rid+'/artifacts/'+'a'*64)[0], 400)
            self.assertEqual(self.request('/api/runs/'+rid+'/resume', {})[0], 200)
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')
            self.assertEqual(executor.calls, 1)
            self.assertIn('markdown', self.request('/api/runs/'+rid+'/report')[1])
            body['goal'] = 'revised request'
            revised = self.request('/api/runs/'+rid+'/revise', body)[1]['id']
            detail = self.wait_run(revised)
            self.assertEqual(detail['run']['data']['parent_run_id'], rid)
            self.assertTrue(any(e['type']=='human_request_revised' for e in detail['events']))
            self.assertEqual(self.request('/api/runs/'+rid)[1]['run']['data']['goal'], 'verify')
            self.request('/api/runs/'+revised+'/cancel', {})
            self.assertEqual(self.wait_run(revised)['run']['status'], 'cancelled')

    def test_pause_requested_during_tool_stops_at_boundary(self):
        entered, release = threading.Event(), threading.Event()
        class SlowExecutor(FakeExecutor):
            def execute(self, *args):
                entered.set()
                release.wait(5)
                return super().execute(*args)
        with patch('masa.application.console.Runner', return_value=SlowExecutor()):
            rid = self.request('/api/runs', {'repo':str(self.source),'goal':'pause'})[1]['id']
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.request('/api/runs', {'repo':str(self.source),'goal':'conflict'})[0], 400)
                self.assertEqual(self.request('/api/runs/'+rid+'/pause', {})[0], 200)
            finally:
                release.set()
            detail = self.wait_run(rid)
            self.assertEqual(detail['run']['status'], 'paused')
            self.assertEqual(detail['steps'][1]['status'], 'pending')
            self.request('/api/runs/'+rid+'/resume', {})
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')

    def test_complete_graph_auto_continues_and_gate_requires_all_checks(self):
        """任一检查失败时其他节点仍取证，Gate 不放行。 Collect all checks but reject any failed evidence."""
        class MixedExecutor(FakeExecutor):
            def execute(self, request, *args):
                result = super().execute(request, *args)
                result['exit_code'] = int(request['operation'] == 'go_vet')
                return result
        with patch('masa.application.console.Runner', return_value=MixedExecutor()):
            status, result = self.request('/api/runs', {'repo': str(self.source), 'goal': 'complete', 'full_checks': True})
            self.assertEqual(status, 200)
            detail = self.wait_run(result['id'])
            self.assertEqual(detail['run']['status'], 'failed')
            self.assertEqual(detail['run']['tool_calls'], 3)
            self.assertEqual(detail['run']['model_calls'], 6)
            self.assertEqual(len(detail['steps']), 4)

    def test_complete_graph_supports_step_then_automatic_resume(self):
        """单步恢复不会重跑完成节点，可再次切换自动推进。 Step-resume preserves completed nodes before auto continuation."""
        executor = FakeExecutor()
        with patch('masa.application.console.Runner', return_value=executor):
            rid = self.request('/api/runs', {'repo': str(self.source), 'goal': 'step', 'full_checks': True, 'pause_after': True})[1]['id']
            self.assertEqual(self.wait_run(rid)['run']['status'], 'paused')
            self.request('/api/runs/'+rid+'/resume', {'pause_after': True})
            detail = self.wait_run(rid)
            self.assertEqual(detail['run']['status'], 'paused')
            self.assertEqual(executor.calls, 2)
            self.request('/api/runs/'+rid+'/resume', {})
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')
            self.assertEqual(executor.calls, 3)

    def test_invalid_run_options_have_no_side_effects(self):
        """错误配置不得创建运行或降级 provider。 Invalid options must not create runs or downgrade providers."""
        for extra in ({'provider': 'lve'}, {'full_checks': 'false'}, {'intelligence': 1}, {'pause_after': None}):
            status, _ = self.request('/api/runs', {'repo': str(self.source), 'goal': 'invalid', **extra})
            self.assertEqual(status, 400)
        self.assertEqual(self.console.list_runs(), [])
        self.assertIsNone(self.console.active)

    def test_readonly_roles_are_explicit_and_reject_live_mode(self):
        """只读角色入口明确隔离真实 provider 与其他模板。 Keep the read-only demo separate from live models and other templates."""
        body = {'repo': str(self.source), 'goal': 'roles', 'role_demo': True}
        for extra in ({'provider': 'live'}, {'full_checks': True}):
            self.assertEqual(self.request('/api/runs', {**body, **extra})[0], 400)
        with patch('masa.application.console.Runner', return_value=FakeExecutor()):
            status, result = self.request('/api/runs', body)
            self.assertEqual(status, 200)
            detail = self.wait_run(result['id'])
            self.assertEqual(detail['run']['status'], 'succeeded')
            self.assertEqual(detail['run']['model_calls'], 5)
            self.assertEqual(len(detail['steps']), 5)
            refs = [e['payload']['handoff_ref'] for e in detail['events'] if e['type'] == 'handoff_received']
            self.assertEqual(len(refs), 3)
            self.assertEqual(self.request('/api/runs/'+result['id']+'/artifacts/'+refs[0])[0], 200)

    def test_generation_http_requires_review_before_execution(self):
        """HTTP 从生成到审核再到验证，恢复按钮不能跳过人工确认。 HTTP generation/review/verification cannot bypass human approval."""
        from test_codegen import ProposalProvider
        with patch.object(self.console.settings, 'provider', return_value=ProposalProvider()), patch('masa.application.console.Runner', return_value=FakeExecutor()):
            status, generated = self.request('/api/generate', {'goal':'Implement Add'})
            self.assertEqual(status, 200)
            rid = generated['id']
            detail = self.request('/api/runs/'+rid)[1]
            self.assertEqual(detail['run']['status'], 'paused')
            self.assertEqual(self.request('/api/runs/'+rid+'/resume', {})[0], 400)
            ref = detail['run']['data']['codegen']['proposal_ref']
            draft = self.request('/api/runs/'+rid+'/artifacts/'+ref)[1]['artifact']
            status, _ = self.request('/api/runs/'+rid+'/review-code', {'action':'approve','proposal_ref':ref,'content':draft['content']})
            self.assertEqual(status, 200)
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')

    def test_cancel_paused_run_while_another_worker_is_active(self):
        """取消旧任务不依赖另一个 worker，也不取消新任务。 Cancelling a paused run must not affect another worker."""
        entered, release = threading.Event(), threading.Event()
        class BlockingExecutor(FakeExecutor):
            def execute(self, *args):
                """阻塞第二次调用以观察并发控制。 Block the second call to inspect concurrent control."""
                if self.calls == 1:
                    entered.set()
                    release.wait(5)
                return super().execute(*args)
        with patch('masa.application.console.Runner', return_value=BlockingExecutor()):
            body = {'repo': str(self.source), 'goal': 'verify', 'pause_after': True}
            first = self.request('/api/runs', body)[1]['id']
            self.assertEqual(self.wait_run(first)['run']['status'], 'paused')
            second = self.request('/api/runs', {**body, 'pause_after': False})[1]['id']
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.request('/api/runs/'+first+'/cancel', {})[0], 200)
                self.assertEqual(self.request('/api/runs/'+first)[1]['run']['status'], 'cancelled')
                self.assertFalse(self.request('/api/runs/'+second)[1]['run']['cancel_requested'])
            finally:
                release.set()
            self.assertEqual(self.wait_run(second)['run']['status'], 'succeeded')
