"""规划契约和不执行边界。 Planning contracts and no-execution boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path
from masa.infrastructure.store import Store
from masa.domain.models import MasaError
from masa.application.planning import ProjectPlanning, validate_spec, validate_checks
from masa.runtime.engine import Runtime
from test_runtime import FakeExecutor


SPEC = {'summary':'Small CLI with separated parsing and logic', 'module':'example.com/task',
        'entrypoint':'cmd/app/main.go', 'files':[
            {'path':'go.mod','purpose':'module'}, {'path':'cmd/app/main.go','purpose':'CLI entry'},
            {'path':'internal/app/app.go','purpose':'business rules'},
            {'path':'internal/app/app_test.go','purpose':'acceptance tests'}],
        'acceptance':['invalid arguments return an error', 'valid input returns expected output']}
CHECKS = [{'operation':op, 'purpose':'verify evidence', 'acceptance_indices':[0,1] if op=='go_test' else []}
          for op in ['go_test','go_vet','go_fmt_check']]


class PlannerProvider:
    profile = {'provider':'test','model':'fake'}
    usage = {'total_tokens':10}

    def __init__(self):
        self.contexts = []

    def respond(self, context):
        self.contexts.append(context)
        return copy.deepcopy(SPEC if context['purpose']=='project_planner' else CHECKS)


class ProjectPlanTests(unittest.TestCase):
    def test_rejects_nonportable_conflicting_paths_and_missing_acceptance(self):
        for path in ['../evil.go','/evil.go','C:/evil.go','con.go','x/COM1.go','x./a.go','go.mod/a.go','GO.MOD']:
            with self.subTest(path=path):
                value=copy.deepcopy(SPEC)
                value['files'].append({'path':path,'purpose':'bad'})
                with self.assertRaises(MasaError): validate_spec(value)
        value=copy.deepcopy(SPEC);value['acceptance']=[]
        with self.assertRaises(MasaError): validate_spec(value)
        checks=copy.deepcopy(CHECKS);checks[0]['acceptance_indices']=[0]
        with self.assertRaises(MasaError): validate_checks(checks,SPEC)
        checks=copy.deepcopy(CHECKS);checks[0]['operation']='shell'
        with self.assertRaises(MasaError): validate_checks(checks,SPEC)

    def test_role_handoff_approval_and_no_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)); executor=FakeExecutor(); provider=PlannerProvider()
            try:
                service=ProjectPlanning(store,executor)
                rid=service.generate(provider,'Build a small CLI')
                run=store.run(rid);plan=run['data']['project_plan']
                self.assertEqual(provider.contexts[1]['spec'],SPEC)
                self.assertEqual(run['model_calls'],2)
                self.assertEqual(run['tool_calls'],0)
                self.assertEqual(run['status'],'paused')
                body={'spec_ref':plan['spec_ref'],'checks_ref':plan['checks_ref'],'spec':copy.deepcopy(SPEC),'checks':CHECKS}
                stale={**body,'spec_ref':'0'*64}
                with self.assertRaises(MasaError): service.approve(rid,stale)
                body['spec']['summary']='Human edited architecture'
                service.approve(rid,body)
                approved=store.read(store.run(rid)['data']['project_plan']['approval_ref'])
                self.assertEqual(approved['spec']['summary'],'Human edited architecture')
                with self.assertRaises(MasaError): Runtime(store,executor).execute(rid)
                self.assertEqual(executor.calls,0)
                self.assertFalse((Path(run['data']['workspace'])/'cmd/app/main.go').exists())
            finally: store.close()

    def test_invalid_planner_never_reaches_tester(self):
        provider=PlannerProvider()
        def invalid(context):
            provider.contexts.append(context)
            return {'summary':'not a spec'}
        provider.respond=invalid
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp))
            try:
                with self.assertRaises(MasaError): ProjectPlanning(store,FakeExecutor()).generate(provider,'Build CLI')
                self.assertEqual(len(provider.contexts),1)
                run_id=store.db.execute('SELECT id FROM runs').fetchone()[0]
                self.assertEqual(store.run(run_id)['status'],'failed')
                self.assertEqual(store.run(run_id)['tool_calls'],0)
            finally: store.close()

    def test_coverage_may_be_shared_across_checks(self):
        checks=copy.deepcopy(CHECKS)
        checks[0]['acceptance_indices']=[0]
        checks[1]['acceptance_indices']=[1]
        self.assertEqual(validate_checks(checks,SPEC),checks)

    def test_agent_selects_graph_and_concrete_case_contract(self):
        checks=[copy.deepcopy(CHECKS[0])]
        checks[0]['cases']=[{'name':'empty','input':'no args','expected':'exit 0, stdout 0','level':'cli'}]
        graph=Runtime.compile_project_checks(SPEC,checks)
        self.assertEqual([n.id for n in graph.nodes],['test','gate'])
        self.assertEqual(graph.nodes[-1].dependencies,('test',))
        with self.assertRaises(MasaError):Runtime.compile_project_checks(SPEC,[CHECKS[1]])
        checks[0]['cases'][0]['expected']=''
        with self.assertRaises(MasaError):validate_checks(checks,SPEC)

    def test_missing_coverage_is_explicit_review_warning_not_success(self):
        provider=PlannerProvider()
        original=provider.respond
        def respond(context):
            result=original(context)
            if context['purpose']=='project_tester':result[0]['acceptance_indices']=[]
            return result
        provider.respond=respond
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp))
            try:
                rid=ProjectPlanning(store,FakeExecutor()).generate(provider,'Build CLI')
                run=store.run(rid);plan=run['data']['project_plan']
                self.assertEqual(run['status'],'paused')
                self.assertIn('coverage_warning',plan)
                self.assertEqual(store.read(plan['checks_ref'])[0]['acceptance_indices'],[0,1])
                self.assertEqual(run['tool_calls'],0)
            finally:store.close()
