import { startJob } from '../../api/jobs';
import type { Detail } from '../../api/types';
import { useActivity } from '../../app/activity';
import type { DerivedStatus } from '../../entities/status';
import { Button, Notice } from '../../shared/ui';
import { ClarificationCard } from '../decisions/cards/ClarificationCard';
import { CodeReviewCard } from '../decisions/cards/CodeReviewCard';
import { PlanCard } from '../decisions/cards/PlanCard';
import { ModelSelect, useModelChoice } from '../decisions/ModelSelect';
import { TriageView } from '../decisions/TriageView';
import { useAction } from '../decisions/useAction';

// “需要你”：只有在流程停下来等你时才出现——澄清问题、确认方案、审阅代码；以及出错与恢复的提示。其余时候这里什么都没有。
// "Needs you": appears only when the flow is waiting for you (clarification, plan approval, code review), plus errors and recovery hints. Otherwise there is nothing here.
export function NeedsYou({ detail, status }: { detail: Detail; status: DerivedStatus }) {
  const run = detail.run;
  const plan = run.data.project_plan;
  const { working } = useActivity();
  const choice = useModelChoice();
  const resume = useAction(() => startJob('/runs/' + run.id + '/resume-project', { api_profile_id: choice.id }, { label: '恢复流程' }));
  const stalled = Boolean(plan) && !status.live && ['planning', 'generating'].includes(plan!.status);
  const card =
    !status.needsUser || !plan ? null : plan.status === 'waiting_for_input' && plan.clarification ? (
      <ClarificationCard key={plan.clarification_id} detail={detail} />
    ) : plan.kind === 'code' ? (
      <CodeReviewCard detail={detail} />
    ) : (
      <PlanCard key={run.id} detail={detail} />
    );
  const triage = plan?.triage && plan.triage.verdict !== 'ok' ? plan.triage : null;

  if (!card && !triage && !stalled && !detail.worker_error && !resume.error) return null;
  return (
    <section className="needs-you" aria-label="需要你处理">
      {detail.worker_error && <Notice tone="bad">后台错误：{detail.worker_error}</Notice>}
      {triage && <TriageView triage={triage} />}
      {stalled && (
        <Notice tone="warn">
          原调用记录已保存，不会自动重发未知请求。
          <span className="row">
            <ModelSelect choice={choice} disabled={resume.busy} />
            <Button size="sm" disabled={resume.busy || working} onClick={resume.run}>检查并恢复流程</Button>
          </span>
        </Notice>
      )}
      {resume.error && <Notice tone="bad">{resume.error.message}</Notice>}
      {card}
    </section>
  );
}
