"""持久检查点状态规则。 Durable workflow checkpoint rules."""
import tempfile
import unittest
from pathlib import Path
from masa.application.orchestration.workflow import WorkflowCheckpoint
from masa.infrastructure.jobs import Jobs


class WorkflowCheckpointTests(unittest.TestCase):
    def test_reopen_preserves_stagnation_and_does_not_double_count(self):
        """重开数据库后同一验证不重计，第三次同断言停止。 Reopening preserves counts and stops on the third identical failure."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);jobs=Jobs(root)
            jobs['job']={'status':'running','phase':'verification','run_id':'first'}
            workflow=WorkflowCheckpoint(jobs['job'])
            self.assertFalse(workflow.record_assertion('first',('expected 7',)))
            jobs=Jobs(root);workflow=WorkflowCheckpoint(jobs['job'])
            self.assertFalse(workflow.record_assertion('first',('expected 7',)))
            self.assertEqual(jobs['job']['repeated_assertions'],1)
            self.assertFalse(workflow.record_assertion('second',('expected 7',)))
            self.assertTrue(workflow.record_assertion('third',('expected 7',)))
            self.assertFalse(workflow.record_assertion('fourth',('expected 8',)))
            workflow.phase('repair','revision',3)
            saved=Jobs(root)['job']
            self.assertEqual((saved['phase'],saved['run_id'],saved['attempt']),('repair','revision',3))
