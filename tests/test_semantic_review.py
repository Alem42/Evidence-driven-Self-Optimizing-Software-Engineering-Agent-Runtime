"""语义评审来源约束和恢复。 Semantic review grounding and recovery."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from masa.domain.test_review import validate_semantic_review
from masa.domain.models import MasaError
from masa.application.semantic_review import review_test_semantics
from masa.application.planning import ProjectPlanning
from masa.infrastructure.store import Store
from test_project_plan import SPEC,CHECKS,PlannerProvider
from test_runtime import FakeExecutor


class SemanticReviewTests(unittest.TestCase):
    def test_finding_requires_actual_referenced_text(self):
        """不接受模型编造的引用。 Reject invented evidence references."""
        finding={'severity':'warning','acceptance_index':0,'check_index':None,'case_index':None,
                 'evidence':'invented text','explanation':'issue','suggestion':'fix'}
        with self.assertRaises(MasaError):validate_semantic_review({'summary':'review','findings':[finding]},SPEC,CHECKS)
        finding['evidence']=SPEC['acceptance'][0]
        validate_semantic_review({'summary':'review','findings':[finding]},SPEC,CHECKS)

    def test_resume_reuses_report_and_preserves_parent(self):
        """完成评审恢复不调用模型、不批准父运行。 Resume a completed review without recalling or approving."""
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp));provider=PlannerProvider();parent=ProjectPlanning(s,FakeExecutor()).generate(provider,'Build CLI')
            with patch.object(provider,'respond',return_value={'summary':'reviewed','findings':[]}):
                result=review_test_semantics(s,FakeExecutor(),provider,parent,SPEC,CHECKS)
            s.close();s=Store(Path(temp))
            with patch.object(provider,'respond') as call:
                saved=review_test_semantics(s,FakeExecutor(),provider,parent,SPEC,CHECKS,resume_id=result['review_id'])
                call.assert_not_called()
            self.assertEqual(saved,result)
            self.assertEqual(s.run(parent)['data']['project_plan']['status'],'awaiting_review')
            self.assertEqual(s.run(result['review_id'])['model_calls'],1)
            s.close()
