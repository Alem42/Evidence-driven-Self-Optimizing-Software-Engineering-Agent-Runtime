import { useState } from 'react';
import { api } from '../../../api/client';
import { startJob } from '../../../api/jobs';
import { useResults } from '../../../api/queries';
import type { Detail } from '../../../api/types';
import { useActivity } from '../../../app/activity';
import { useOpenRun } from '../../../app/nav';
import { Button, Card, Notice } from '../../../shared/ui';
import { ModelSelect, useModelChoice } from '../ModelSelect';
import { useAction } from '../useAction';

// 验证卡：真实检查结论 + 重新验证/停止 + 失败后的修复入口 + 运行程序。
// Verification card: real check outcome, rerun/stop, repair entry points and running the program.
export function VerifyCard({ detail }: { detail: Detail }) {
  const run = detail.run;
  const open = useOpenRun();
  const { working } = useActivity();
  const results = useResults(detail);
  const checks = results.data?.checks ?? [];
  const finished = checks.filter((c) => c.result);
  const failed = finished.filter((c) => c.result!.status !== 'completed' || c.result!.exit_code !== 0);
  const live = detail.active;

  const rerun = useAction(() => api<{ id: string }>('/runs/' + run.id + '/rerun', {}), (r) => open(r.id));
  const resume = useAction(() => api('/runs/' + run.id + '/resume', {}));
  const cancel = useAction(() => api('/runs/' + run.id + '/cancel', {}));
  const folder = useAction(() => api('/runs/' + run.id + '/open-workspace', {}));
  const tone = failed.length ? 'bad' : run.status === 'succeeded' ? 'ok' : live ? 'running' : undefined;

  return (
    <Card
      title="Executor / Gate · 真实验证"
      tone={tone}
      subtitle={`${finished.length} / ${checks.length} 项工具已返回。${failed.length ? failed.length + ' 项未通过。' : run.status === 'succeeded' ? 'Gate 已通过。' : ''}检查在隔离副本执行，通过不等于业务一定正确。`}
      actions={
        <>
          <Button size="sm" variant="ghost" onClick={() => open(run.id, { tab: 'checks' })}>查看结果 →</Button>
          <Button size="sm" disabled={folder.busy || working} onClick={folder.run}>打开代码文件夹 ↗</Button>
          {live ? (
            <Button size="sm" variant="danger" disabled={cancel.busy} onClick={cancel.run}>停止验证</Button>
          ) : ['created', 'paused', 'running'].includes(run.status) ? (
            <Button size="sm" variant="primary" disabled={resume.busy || working} onClick={resume.run}>继续验证</Button>
          ) : (
            <Button size="sm" disabled={rerun.busy || working || !['succeeded', 'failed', 'needs_attention'].includes(run.status)} onClick={rerun.run}>重新验证</Button>
          )}
        </>
      }
    >
      {[rerun.error, resume.error, cancel.error, folder.error].filter(Boolean).map((e, i) => <Notice key={i} tone="bad">{(e as Error).message}</Notice>)}
      {run.status === 'failed' && <RepairBox detail={detail} advice={results.data?.repair_advice} />}
      {run.status === 'succeeded' && <AppRunner detail={detail} />}
    </Card>
  );
}

function RepairBox({ detail, advice }: { detail: Detail; advice?: { action: string; message: string } }) {
  const { working } = useActivity();
  const choice = useModelChoice();
  const open = useOpenRun();
  const [feedback, setFeedback] = useState('');
  const body = { feedback, api_profile_id: choice.id };
  const repair = useAction(() => startJob('/runs/' + detail.run.id + '/repair-project', body, { label: '修复实现' }));
  const revise = useAction(() => startJob('/runs/' + detail.run.id + '/revise-project-tests', body, { label: '修订测试' }));
  const format = useAction(() => api<{ id: string }>('/runs/' + detail.run.id + '/format-project-tests', {}), (r) => open(r.id, { tab: 'code' }));
  const formatOnly = advice?.action === 'format_tests';
  const cycle = formatOnly || advice?.action === 'revise_tests';
  const busy = repair.busy || revise.busy || format.busy;
  const error = repair.error ?? revise.error ?? format.error;

  return (
    <div className="repair">
      <h4>下一步：修复</h4>
      <p className="muted">修改实现（测试冻结），或在测试本身有缺陷时创建单独的测试修订；每次修订都是新版本并重新验证。</p>
      {advice && <Notice tone={cycle ? 'warn' : 'neutral'}>{advice.message}</Notice>}
      <textarea rows={2} maxLength={4000} value={feedback} disabled={busy} onChange={(e) => setFeedback(e.target.value)} placeholder="补充说明（可选）：例如保留现有接口，修复编译错误，不更改测试预期。" />
      {error && <Notice tone="bad">{error.message}</Notice>}
      <div className="row wrap">
        <ModelSelect choice={choice} disabled={busy} />
        {formatOnly && <Button variant="primary" disabled={busy} onClick={format.run}>格式化测试文件（无模型调用）</Button>}
        <Button variant={cycle ? 'default' : 'primary'} disabled={busy || working || !advice || cycle || !choice.any} onClick={repair.run}>修复实现（冻结测试）</Button>
        <Button variant={cycle && !formatOnly ? 'primary' : 'default'} disabled={busy || working || !choice.any} onClick={revise.run}>修订测试（新版本）</Button>
      </div>
    </div>
  );
}

// 一行一个参数，避免 shell 解析；程序输出与测试结论相互独立。
function AppRunner({ detail }: { detail: Detail }) {
  const [args, setArgs] = useState('');
  const [result, setResult] = useState<any>(null);
  const start = useAction(
    () =>
      new Promise<void>((resolve, reject) => {
        startJob('/runs/' + detail.run.id + '/run-application', { argv: args === '' ? [] : args.split('\n') }, {
          label: '运行程序',
          onDone: (job) => {
            if (job.status === 'failed') reject(new Error(job.error ?? '运行失败'));
            else {
              setResult(job.result?.app_result);
              resolve();
            }
          },
        }).catch(reject);
      }),
  );
  const stop = useAction(() => api('/runs/' + detail.run.id + '/stop-application', {}));
  return (
    <div className="repair">
      <h4>运行这个程序</h4>
      <textarea rows={2} value={args} disabled={start.busy} onChange={(e) => setArgs(e.target.value)} placeholder="程序参数，每行一个；留空表示无参数" />
      <div className="row">
        <Button variant="primary" size="sm" disabled={start.busy} onClick={start.run}>{start.busy ? '正在构建并运行…' : '运行程序'}</Button>
        {start.busy && <Button size="sm" onClick={stop.run}>停止</Button>}
        <span className="hint">最多 30 秒；不改变测试与 Gate 结论。</span>
      </div>
      {start.error && <Notice tone="bad">{start.error.message}</Notice>}
      {result && (
        <div className="app-output">
          <div className="muted">{result.phase === 'build' ? '构建' : '运行'} · {result.status} · 退出码 {result.exit_code ?? '未知'}</div>
          <div className="field-label">标准输出</div>
          <pre>{result.stdout || '（空）'}</pre>
          <div className="field-label">错误输出</div>
          <pre>{result.stderr || result.error || '（空）'}</pre>
          {result.truncated && <div className="muted">输出超过上限，已截断。</div>}
        </div>
      )}
    </div>
  );
}
