"""测试计划审查与编排边界。 Test review and coordinator boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path
from masa.application.test_review import review_test_plan
from masa.application.coordinator import WorkflowCoordinator
from masa.application.planning import ProjectPlanning
from masa.infrastructure.store import Store
from masa.infrastructure.jobs import Jobs
from masa.domain.models import MasaError
from test_project_plan import SPEC,CHECKS,PlannerProvider
from test_auto_project import CompositeProvider
from test_runtime import FakeExecutor


class ReviewTests(unittest.TestCase):
    def test_random_distinct_expectation_blocks_but_range_does_not(self):
        """随机结果不保证不同，范围性质可以确定验证。 Random repeats are valid while range properties are testable."""
        spec=copy.deepcopy(SPEC);spec['summary']='Random integer CLI'
        checks=copy.deepcopy(CHECKS)
        case={'name':'random','input':'run 10 times','expected':'all outputs must be different','level':'cli'}
        checks[0]['cases']=[case]
        self.assertEqual(review_test_plan(spec,checks)['status'],'blocked')
        case['expected']='each integer is in inclusive range 0..99, repeats allowed'
        self.assertEqual(review_test_plan(spec,checks)['status'],'reviewed')

    def test_blocked_review_prevents_approval_without_tools(self):
        """可疑测试必须修正后才批准，不能直接进入代码生成。 Suspicious tests cannot advance through approval."""
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp));p=ProjectPlanning(s,FakeExecutor());rid=p.generate(PlannerProvider(),'Build CLI')
            plan=s.run(rid)['data']['project_plan'];spec=copy.deepcopy(SPEC);spec['summary']='Random CLI'
            checks=copy.deepcopy(CHECKS);checks[0]['cases']=[{'name':'random','input':'','expected':'every run returns different output','level':'cli'}]
            with self.assertRaisesRegex(MasaError,'test plan review blocked'):
                p.approve(rid,{'spec_ref':plan['spec_ref'],'checks_ref':plan['checks_ref'],'spec':spec,'checks':checks})
            self.assertEqual(s.run(rid)['data']['project_plan']['status'],'awaiting_review')
            self.assertEqual(s.run(rid)['tool_calls'],0)
            s.close()

    def test_coordinator_runs_without_console_or_http(self):
        """直接运行编排器仍经过真实 Runtime Gate。 Run the coordinator directly through the Runtime Gate."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=Store(root);jobs=Jobs(root)
            jobs['direct']={'status':'running','phase':'planning','attempt':0,'run_id':None,'request':{'goal':'Build CLI'}}
            WorkflowCoordinator(s,FakeExecutor(),CompositeProvider(),jobs['direct']).run()
            self.assertEqual(jobs['direct']['status'],'completed')
            self.assertEqual(s.run(jobs['direct']['result']['id'])['status'],'succeeded')
            s.close()
