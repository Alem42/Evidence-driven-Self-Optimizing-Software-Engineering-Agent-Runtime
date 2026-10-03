import { startJob } from '../../api/jobs';
import type { Detail } from '../../api/types';
import { useActivity } from '../../app/activity';
import type { DerivedStatus } from '../../entities/status';
import { Button, Card, Notice } from '../../shared/ui';
import { TriageView } from './TriageView';
import { ActivityLog } from './ActivityLog';
import { ModelSelect, useModelChoice } from './ModelSelect';
import { useAction } from './useAction';
import { ClarificationCard } from './cards/ClarificationCard';
import { CodeReviewCard } from './cards/CodeReviewCard';
import { PlanCard } from './cards/PlanCard';
import { VerifyCard } from './cards/VerifyCard';

// 对话 · 决策流：需求 → 当前需要你处理的决策卡 → 验证；每张卡只出现在适用的状态下。
// Decision thread: requirement → the decision card that applies now → verification.
export function Thread({ detail, status }: { detail: Detail; status: DerivedStatus }) {
  const run = detail.run;
  const plan = run.data.project_plan;
  const { working } = useActivity();
  const choice = useModelChoice();
  const resume = useAction(() => startJob('/runs/' + run.id + '/resume-project', { api_profile_id: choice.id }, { label: '恢复流程' }));

  return (
    <div className="thread">
      <Card title="需求" className="req">
        <p className="goal">{run.data.goal}</p>
      </Card>

      {plan?.triage && <TriageView triage={plan.triage} />}

      {detail.worker_error && <Notice tone="bad">后台错误：{detail.worker_error}</Notice>}

      {status.live && (
        <Notice tone="warn">
          <strong>{status.headline}</strong> · {status.hint}
        </Notice>
      )}

      {plan && !status.live && ['planning', 'generating'].includes(plan.status) && (
        <Notice tone="warn">
          原调用记录已保存，不会自动重发未知请求。
          <span className="row">
            <ModelSelect choice={choice} disabled={resume.busy} />
            <Button size="sm" disabled={resume.busy || working} onClick={resume.run}>检查并恢复流程</Button>
          </span>
        </Notice>
      )}
      {resume.error && <Notice tone="bad">{resume.error.message}</Notice>}

      {plan?.status === 'waiting_for_input' && plan.clarification ? (
        <ClarificationCard key={plan.clarification_id} detail={detail} />
      ) : plan?.kind === 'code' ? (
        <CodeReviewCard detail={detail} />
      ) : plan ? (
        <PlanCard key={run.id} detail={detail} />
      ) : null}

      {run.data.project_bundle && <VerifyCard detail={detail} />}

      <ActivityLog detail={detail} />
    </div>
  );
}
