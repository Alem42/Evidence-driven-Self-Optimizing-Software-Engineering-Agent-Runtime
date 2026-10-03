import { useEffect, useState } from 'react';
import { api } from '../../../api/client';
import { startJob } from '../../../api/jobs';
import { useArtifact } from '../../../api/queries';
import type { Detail } from '../../../api/types';
import { useActivity } from '../../../app/activity';
import { Button, Card, Notice } from '../../../shared/ui';
import { ModelSelect, useModelChoice } from '../ModelSelect';
import { useAction } from '../useAction';

interface Spec {
  summary: string;
  module: string;
  entrypoint: string;
  files: { path: string; purpose: string }[];
  acceptance: string[];
}
interface Check {
  operation: string;
  purpose: string;
  acceptance_indices: number[];
  cases?: { level: string; name: string; input: string; expected: string }[];
}

// 方案卡：结构化编辑项目结构、验收标准与测试方案；批准后可一键生成代码。
// Plan card: structured editing of structure, acceptance criteria and test plan.
export function PlanCard({ detail }: { detail: Detail }) {
  const run = detail.run;
  const plan = run.data.project_plan!;
  const approved = useArtifact<{ spec: Spec; checks: Check[] }>(run.id, plan.approval_ref);
  const specArt = useArtifact<Spec>(run.id, plan.approval_ref ? undefined : plan.spec_ref);
  const checksArt = useArtifact<Check[]>(run.id, plan.approval_ref ? undefined : plan.checks_ref);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [saved, setSaved] = useState(false);
  const choice = useModelChoice();
  const { working } = useActivity();

  const loadedSpec = approved.data?.spec ?? specArt.data;
  const loadedChecks = approved.data?.checks ?? checksArt.data;
  useEffect(() => {
    if (loadedSpec) {
      setSpec(structuredClone(loadedSpec));
      setChecks(structuredClone(loadedChecks ?? []));
      setSaved(false);
    }
  }, [loadedSpec, loadedChecks, run.id]);

  const editable = plan.status === 'awaiting_review' && run.status === 'paused' && !run.cancel_requested && !saved;
  const generate = () => startJob('/runs/' + run.id + '/generate-project', { api_profile_id: choice.id }, { label: '生成代码' });
  const approveOnly = useAction(() => api('/runs/' + run.id + '/approve-project', { spec, checks, spec_ref: plan.spec_ref, checks_ref: plan.checks_ref }), () => setSaved(true));
  const approveAndGenerate = useAction(async () => {
    await api('/runs/' + run.id + '/approve-project', { spec, checks, spec_ref: plan.spec_ref, checks_ref: plan.checks_ref });
    setSaved(true);
    await generate();
  });
  const generateOnly = useAction(generate);
  const retry = useAction(() => startJob('/projects/plan', { retry_run_id: run.id, api_profile_id: choice.id }, { label: '重试验证方案' }));
  const busy = approveOnly.busy || approveAndGenerate.busy || generateOnly.busy || retry.busy;
  const error = approveOnly.error ?? approveAndGenerate.error ?? generateOnly.error ?? retry.error;
  const canGenerate = (plan.status === 'approved' || saved) && plan.review_mode !== 'automatic';

  const update = (patch: Partial<Spec>) => spec && setSpec({ ...spec, ...patch });

  return (
    <Card
      title="Planner / Tester · 项目方案"
      tone={editable ? 'wait' : undefined}
      subtitle={spec ? `${spec.files.length} 个计划文件 · ${spec.acceptance.length} 条验收标准 · ${checks.length} 项拟执行检查。Tester 此时只设计测试，尚未运行 Go。` : '正在读取方案…'}
      actions={
        <>
          <ModelSelect choice={choice} disabled={busy} />
          {editable && (
            <Button variant="primary" disabled={busy || working || !choice.any} onClick={approveAndGenerate.run}>
              {approveAndGenerate.busy ? '处理中…' : '确认并生成代码'}
            </Button>
          )}
          {canGenerate && (
            <Button variant="primary" disabled={busy || working || !choice.any} onClick={generateOnly.run}>
              根据已确认方案生成代码
            </Button>
          )}
        </>
      }
    >
      {plan.coverage_warning && <Notice tone="warn">{plan.coverage_warning}</Notice>}
      {!!plan.test_review?.findings?.length && (
        <Notice tone="warn">
          <strong>独立测试计划评审</strong>
          <ul>
            {plan.test_review.findings.map((f, i) => (
              <li key={i}>{f.severity === 'blocking' ? '需修正' : '建议检查'}：{f.message}</li>
            ))}
          </ul>
          <div className="muted">规则评审发现潜在问题；它不能证明测试正确或代码通过。</div>
        </Notice>
      )}
      {(error || plan.error) && <Notice tone="bad">{error?.message || plan.error}</Notice>}
      {plan.status === 'failed' && plan.spec_ref && (
        <div><Button disabled={busy} onClick={retry.run}>保留架构，仅重试验证方案</Button></div>
      )}
      {spec && (
        <fieldset className="plan-form" disabled={!editable || busy}>
          <label className="field">
            <span className="field-label">设计说明</span>
            <textarea rows={3} value={spec.summary} onChange={(e) => update({ summary: e.target.value })} />
          </label>
          <div className="grid2">
            <label className="field">
              <span className="field-label">Go 模块名</span>
              <input value={spec.module} onChange={(e) => update({ module: e.target.value })} />
            </label>
            <div className="field">
              <span className="field-label">固定入口</span>
              <code className="static">{spec.entrypoint}</code>
            </div>
          </div>

          <h4>目录与文件职责</h4>
          {spec.files.map((f, i) => (
            <div className="row-edit" key={i}>
              <input aria-label="文件路径" value={f.path} onChange={(e) => update({ files: spec.files.map((v, n) => (n === i ? { ...v, path: e.target.value } : v)) })} />
              <input aria-label="职责" value={f.purpose} onChange={(e) => update({ files: spec.files.map((v, n) => (n === i ? { ...v, purpose: e.target.value } : v)) })} />
              <button className="icon-btn" aria-label="删除文件" onClick={() => update({ files: spec.files.filter((_, n) => n !== i) })}>×</button>
            </div>
          ))}
          {editable && <Button size="sm" variant="ghost" onClick={() => update({ files: [...spec.files, { path: '', purpose: '' }] })}>＋ 添加文件</Button>}

          <h4>验收标准</h4>
          {spec.acceptance.map((a, i) => (
            <div className="row-edit" key={i}>
              <span className="idx">#{i + 1}</span>
              <textarea rows={2} aria-label={`验收标准 ${i + 1}`} value={a} onChange={(e) => update({ acceptance: spec.acceptance.map((v, n) => (n === i ? e.target.value : v)) })} />
              <button className="icon-btn" aria-label="删除验收标准" onClick={() => update({ acceptance: spec.acceptance.filter((_, n) => n !== i) })}>×</button>
            </div>
          ))}
          {editable && <Button size="sm" variant="ghost" onClick={() => update({ acceptance: [...spec.acceptance, ''] })}>＋ 添加验收标准</Button>}

          <h4>Tester · 验证方案</h4>
          {checks.map((c, i) => (
            <div className="check-plan" key={c.operation}>
              <div className="field-label">{c.operation} · 覆盖验收项 {c.acceptance_indices.map((n) => n + 1).join('、') || '通用检查'}</div>
              <textarea rows={2} value={c.purpose} onChange={(e) => setChecks(checks.map((v, n) => (n === i ? { ...v, purpose: e.target.value } : v)))} />
              {c.cases?.map((t, n) => (
                <details key={n}>
                  <summary>{t.level} · {t.name}</summary>
                  <p>输入：{t.input}</p>
                  <p>预期：{t.expected}</p>
                </details>
              ))}
            </div>
          ))}
        </fieldset>
      )}
      {editable && (
        <div className="row"><Button size="sm" disabled={busy} onClick={approveOnly.run}>仅保存确认（稍后再生成）</Button></div>
      )}
    </Card>
  );
}
