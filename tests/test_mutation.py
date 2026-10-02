"""变异状态分类验证。 Mutation verdict classification tests."""
import tempfile
import unittest
from pathlib import Path
from masa.application.mutation import evaluate_mutations
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.runtime.engine import Runtime
from masa.infrastructure.store import Store
from test_project_plan import PlannerProvider,SPEC,CHECKS
from test_project_generation import DeveloperProvider
from test_runtime import FakeExecutor

class MutationTests(unittest.TestCase):
    def test_survivor_compile_error_and_timeout_are_not_kills(self):
        """编译失败和超时不能冒充测试发现错误。 Build failures and timeouts are not killed mutants."""
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp));p=ProjectPlanning(s,FakeExecutor());plan=p.generate(PlannerProvider(),'Build CLI');m=s.run(plan)['data']['project_plan']
            p.approve(plan,{'spec_ref':m['spec_ref'],'checks_ref':m['checks_ref'],'spec':SPEC,'checks':CHECKS})
            g=ProjectGeneration(s,FakeExecutor());draft=g.generate(plan,DeveloperProvider());m=s.run(draft)['data']['project_plan'];rid=g.approve(draft,{'files_ref':m['files_ref'],'files':s.read(m['files_ref'])});Runtime(s,FakeExecutor()).execute(rid)
            class Evaluator(FakeExecutor):
                def __init__(self):super().__init__();self.calls=0
                def execute(self,request,workspace,cancelled):
                    r=super().execute(request,workspace,cancelled)
                    if self.calls==3:r.update(exit_code=1,stdout='{"Action":"build-fail"}\n')
                    if self.calls==4:r.update(status='timeout',exit_code=None)
                    return r
            probes=[{'name':str(i),'path':'internal/app/app.go','before':'return 42','after':'return '+str(i)} for i in (1,2,3)]
            report=evaluate_mutations(s,Evaluator(),rid,probes)
            self.assertEqual([r['status'] for r in report['results']],['survived','invalid','inconclusive'])
            self.assertEqual(s.run(rid)['status'],'succeeded')
            s.close()
