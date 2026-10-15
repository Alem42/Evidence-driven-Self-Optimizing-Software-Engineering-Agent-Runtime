import { useState } from 'react';
import { startJob } from '../../../api/jobs';
import type { Detail } from '../../../api/types';
import { useActivity } from '../../../app/activity';
import { Button, Card, Notice } from '../../../shared/ui';
import { ModelSelect, useModelChoice } from '../ModelSelect';
import { useAction } from '../useAction';

type Answer = { option_id?: string; text?: string };

// Planner 的澄清问题：选项以卡片呈现，也可自由填写；回答不等于批准方案，也不预选。
// Planner's clarification: option cards or free text; answering never approves and nothing is preselected.
export function ClarificationCard({ detail }: { detail: Detail }) {
  const plan = detail.run.data.project_plan!;
  const clarification = plan.clarification!;
  const choice = useModelChoice();
  const { working } = useActivity();
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const submit = useAction(() =>
    startJob('/runs/' + detail.run.id + '/answer-clarification', { question_id: plan.clarification_id, answers, api_profile_id: choice.id }, { label: '继续规划' }),
  );
  const complete = clarification.questions.every((q) => answers[q.key]?.option_id || answers[q.key]?.text?.trim());

  return (
    <Card title="Planner 需要你补充需求" tone="wait" subtitle={clarification.reason}>
      {clarification.questions.map((q) => (
        <fieldset key={q.key} className="question" disabled={submit.busy}>
          <legend>{q.title}</legend>
          <div className="option-grid">
            {q.options.map((o) => (
              <label key={o.id} className={`option ${answers[q.key]?.option_id === o.id ? 'on' : ''}`}>
                <input type="radio" name={q.key} checked={answers[q.key]?.option_id === o.id} onChange={() => setAnswers({ ...answers, [q.key]: { option_id: o.id } })} />
                {o.label}
              </label>
            ))}
          </div>
          <textarea
            rows={2}
            maxLength={2000}
            placeholder="或者自行填写…"
            value={answers[q.key]?.text ?? ''}
            onChange={(e) => setAnswers({ ...answers, [q.key]: { text: e.target.value } })}
          />
        </fieldset>
      ))}
      {submit.error && <Notice tone="bad">{submit.error.message}</Notice>}
      <div className="row between wrap">
        <span className="hint">回答用于明确需求，不代表批准方案。</span>
        <div className="row">
          <ModelSelect choice={choice} disabled={submit.busy} />
          <Button variant="primary" disabled={submit.busy || working || !complete || !choice.any} onClick={submit.run}>
            {submit.busy ? '提交中…' : '提交回答并继续'}
          </Button>
        </div>
      </div>
    </Card>
  );
}
