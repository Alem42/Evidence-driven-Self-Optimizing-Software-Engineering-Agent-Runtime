"""澄清等待与恢复测试。 Clarification wait and recovery tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from masa.application.planning import ProjectPlanning
from masa.application.console import Console
from masa.infrastructure.store import Store
from masa.domain.models import MasaError
from test_auto_project import CompositeProvider
from test_runtime import FakeExecutor

QUESTION={'kind':'clarification_request','reason':'Need the bounds','questions':[{'key':'range','title':'Which range?','options':[{'id':'small','label':'0 to 99'}],'allow_text':True}]}
ANSWER={'range':{'option_id':'small'}}

class AskingProvider(CompositeProvider):
    def respond(self,context):
        if context['purpose']=='project_planner' and not context.get('answers'):return QUESTION
        return super().respond(context)

class ClarificationTests(unittest.TestCase):
    def test_wait_answer_reopen_and_conflict(self):
        """问题跨重启保留，回答幂等且不跳过审核。 Persist questions across restarts without bypassing review."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=Store(root);provider=AskingProvider()
            rid=ProjectPlanning(s,FakeExecutor()).generate(provider,'Build CLI')
            plan=s.run(rid)['data']['project_plan'];self.assertEqual(plan['status'],'waiting_for_input')
            self.assertEqual(s.run(rid)['model_calls'],1)
            s.close();s=Store(root);p=ProjectPlanning(s,FakeExecutor())
            for _ in range(2):p.answer(rid,plan['clarification_id'],ANSWER)
            with self.assertRaises(MasaError):p.answer(rid,plan['clarification_id'],{'range':{'text':'different'}})
            with self.assertRaises(MasaError):p.answer(rid,'stale',ANSWER)
            p.generate(provider,'Build CLI',resume_id=rid)
            self.assertEqual(s.run(rid)['model_calls'],3)
            self.assertEqual(s.run(rid)['data']['project_plan']['status'],'awaiting_review')
            s.close()

    def test_auto_waits_and_resumes_after_answer(self):
        """自动执行也等待真实回答，再继续整个流程。 Automatic workflows wait for explicit answers."""
        with tempfile.TemporaryDirectory() as temp:
            c=Console(Path(temp),'unused','unused',Path.cwd());c.settings.provider=lambda *_:AskingProvider()
            with patch('masa.application.console.Runner',lambda *args:FakeExecutor()):
                ident=c.start_autonomous_project_job({'goal':'Build CLI'})['job_id'];c.job_thread.join(10)
                job=c.project_job(ident);self.assertEqual(job['status'],'waiting_for_input')
                rid=job['run_id'];plan=c.detail(rid)['run']['data']['project_plan']
                c.answer_clarification(rid,{'question_id':plan['clarification_id'],'answers':ANSWER})
                c.job_thread.join(10)
                self.assertEqual(c.project_job(ident)['status'],'completed',c.project_job(ident))
            c.close()
