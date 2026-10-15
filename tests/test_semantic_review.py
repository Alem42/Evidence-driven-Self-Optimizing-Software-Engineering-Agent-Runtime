"""语义评审来源约束和恢复。 Semantic review grounding and recovery."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from masa.domain.test_review import validate_semantic_review
from masa.domain.models import MasaError
from masa.application.review.semantic_review import review_test_semantics
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

    def test_field_reference_resolves_original_and_rejects_conflicts(self):
        """字段引用提取原文，冲突和越界字段拒绝。 Resolve source text and reject conflicting references."""
        finding={'severity':'warning','acceptance_index':0,'check_index':None,'case_index':None,
                 'evidence_field':'acceptance','explanation':'issue','suggestion':'fix'}
        report={'summary':'review','findings':[finding]}
        validate_semantic_review(report,SPEC,CHECKS)
        self.assertEqual(finding['evidence'],SPEC['acceptance'][0])
        validate_semantic_review(report,SPEC,CHECKS)
        finding['evidence']='invented'
        with self.assertRaises(MasaError):validate_semantic_review(report,SPEC,CHECKS)
        finding.pop('evidence');finding['evidence_field']='expected'
        with self.assertRaises(MasaError):validate_semantic_review(report,SPEC,CHECKS)

    def test_source_ids_and_business_failure_are_persisted(self):
        """来源解析幂等，失败状态持久化且不重放。 Resolve IDs idempotently and persist terminal failures."""
        from masa.domain.test_review import review_sources
        finding={'severity':'warning','source_id':'acceptance:0','explanation':'issue','suggestion':'fix'}
        report={'summary':'review','findings':[finding]}
        validate_semantic_review(report,SPEC,CHECKS);validate_semantic_review(report,SPEC,CHECKS)
        self.assertEqual(finding['evidence'],review_sources(SPEC,CHECKS)['acceptance:0'])
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp));provider=PlannerProvider();parent=ProjectPlanning(s,FakeExecutor()).generate(provider,'Build CLI');ids=[]
            with patch.object(provider,'respond',side_effect=MasaError('invalid response')):
                with self.assertRaisesRegex(MasaError,'semantic review failed'):
                    review_test_semantics(s,FakeExecutor(),provider,parent,SPEC,CHECKS,on_created=ids.append)
            rid=ids[0];self.assertEqual(s.run(rid)['status'],'failed')
            self.assertEqual(s.run(rid)['data']['semantic_review']['status'],'failed')
            self.assertTrue(any(e['type']=='semantic_review_failed' for e in s.events(rid)))
            s.close();s=Store(Path(temp))
            with patch.object(provider,'respond') as call:
                with self.assertRaisesRegex(MasaError,'explicit new review'):
                    review_test_semantics(s,FakeExecutor(),provider,parent,SPEC,CHECKS,resume_id=rid)
                call.assert_not_called()
            s.close()
        finding['source_id']='unknown'
        with self.assertRaises(MasaError):validate_semantic_review(report,SPEC,CHECKS)
