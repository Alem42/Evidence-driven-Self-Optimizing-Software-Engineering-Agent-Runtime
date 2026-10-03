import { api } from '../../../api/client';
import type { Detail } from '../../../api/types';
import { useOpenRun } from '../../../app/nav';
import { useActivity } from '../../../app/activity';
import { useDrafts } from '../../../stores/ui';
import { Button, Card, Notice } from '../../../shared/ui';
import { useCodeFiles, useSourceReview } from '../../code/useCode';
import { useAction } from '../useAction';

/** 批准整套代码：提交的正是当前可见（含本地编辑）的完整文件集合。 Approve exactly what is visible. */
export function ApproveCodeButton({ detail }: { detail: Detail }) {
  const plan = detail.run.data.project_plan!;
  const code = useCodeFiles(detail);
  const open = useOpenRun();
  const { working } = useActivity();
  const approve = useAction(
    () => api<{ id: string }>('/runs/' + detail.run.id + '/approve-project-code', { files_ref: plan.files_ref, files: code.files }),
    (r) => {
      useDrafts.getState().reset(detail.run.id);
      open(r.id, { tab: 'checks' });
    },
  );
  const approved = plan.status === 'approved';
  return (
    <Button variant="primary" size="sm" disabled={approve.busy || working || !code.files || (!code.editable && !approved)} onClick={approve.run}>
      {approve.busy ? '准备项目与验证…' : approved ? '查看已批准项目' : '批准并验证'}
    </Button>
  );
}

// 代码审核卡：摘要与风险提示；真正的阅读和编辑在“代码”标签。
// Code review card: summary and checks; reading/editing happens in the Code tab.
export function CodeReviewCard({ detail }: { detail: Detail }) {
  const plan = detail.run.data.project_plan!;
  const code = useCodeFiles(detail);
  const review = useSourceReview(detail);
  const open = useOpenRun();
  const { working } = useActivity();
  const check = useAction(
    () => api<{ findings: { path: string; line?: number; message: string }[] }>('/runs/' + detail.run.id + '/review-project-sources', { files_ref: plan.files_ref, files: code.files }),
    (r) => review.set(r),
  );
  const title = plan.revision_scope === 'tests' ? 'Tester · 测试修订审核' : plan.repair_of ? 'Developer · 修复版本审核' : 'Developer · 代码审核';
  const names = Object.keys(code.files ?? {});
  const generating = plan.status === 'generating';

  return (
    <Card
      title={title}
      tone={plan.status === 'awaiting_review' ? 'wait' : undefined}
      subtitle={generating ? '正在生成文件…' : '检查后批准；批准会创建新的验证版本，Gate 按真实退出码判定。'}
      actions={code.editable && <ApproveCodeButton detail={detail} />}
    >
      {plan.error && <Notice tone="bad">{plan.error}</Notice>}
      {plan.repair_of && (
        <Notice tone="warn">
          本次改动：{plan.changed_files?.join('、') || '生成中'}。{plan.revision_scope === 'tests' ? '仅测试文件可修改，请确认断言没有被削弱。' : '测试与模块文件冻结。'}
          <button className="link" onClick={() => open(plan.repair_of!, { manual: true, tab: 'history' })}>查看失败版本</button>
        </Notice>
      )}
      {plan.gen_progress && generating && (
        <div className="progress-line">
          <progress value={plan.gen_progress.completed} max={plan.gen_progress.total} />
          <span>文件 {plan.gen_progress.completed} / {plan.gen_progress.total}{plan.gen_progress.current ? ' · ' + plan.gen_progress.current : ''}</span>
        </div>
      )}
      {names.length > 0 && (
        <div className="file-chips">
          {names.map((p) => (
            <button key={p} className="chip" onClick={() => open(detail.run.id, { params: { tab: 'code', file: p } })}>
              {p.endsWith('_test.go') ? '◇ ' : '◻ '}{p}
              {code.changed.has(p) && ' ·改动'}
            </button>
          ))}
        </div>
      )}
      {code.editable && (
        <div className="row wrap">
          <Button size="sm" disabled={check.busy || working || !code.files} onClick={check.run}>{check.busy ? '检查中…' : '检查源码语法与空测试'}</Button>
          <Button size="sm" variant="ghost" onClick={() => open(detail.run.id, { tab: 'code' })}>在代码标签中编辑 →</Button>
          {code.dirty && <span className="tag tag-accent">有未批准的编辑</span>}
        </div>
      )}
      {check.error && <Notice tone="bad">{check.error.message}</Notice>}
      {review.report && (
        <Notice tone={review.report.findings.length ? 'warn' : 'ok'}>
          <strong>{review.report.findings.length ? '源码检查发现问题' : '源码解析完成'}</strong>
          {review.report.findings.map((f, i) => (
            <div key={i}>
              <button className="link" onClick={() => open(detail.run.id, { params: { tab: 'code', file: f.path, ...(f.line ? { line: String(f.line) } : {}) } })}>
                {f.path}{f.line ? ':' + f.line : ''}
              </button>{' '}
              {f.message}
            </div>
          ))}
          <div className="muted">只检查语法与空测试；导入、类型和业务断言仍需后续真实验证。</div>
        </Notice>
      )}
    </Card>
  );
}
