import { useOpenRun } from '../../app/nav';
import { useResults } from '../../api/queries';
import type { Detail } from '../../api/types';
import { failureSummary, parseDiagnostics, readableOutput } from '../../entities/text';
import { Badge, Empty, Spinner } from '../../shared/ui';

const NAMES: Record<string, string> = { go_test: '测试与编译', go_vet: '静态检查', go_fmt_check: '代码格式', go_index: '代码索引' };

// 验证标签：真实工具结果；失败线索可点击跳转到代码对应行。
// Verification tab: real tool results; failure hints jump to the exact source line.
export function VerificationView({ detail }: { detail: Detail }) {
  const results = useResults(detail.run.data.project_bundle ? detail : undefined);
  const open = useOpenRun();
  if (!detail.run.data.project_bundle)
    return <Empty title="尚未执行工具检查">规划阶段 Tester 只设计测试用例。完整代码发布后，Executor 才运行 go test、vet 与格式检查。</Empty>;
  if (results.isLoading) return <div className="center pad"><Spinner /> 读取结果…</div>;
  const checks = results.data?.checks ?? [];
  const finished = checks.filter((c) => c.result);
  const failed = finished.filter((c) => c.result!.status !== 'completed' || c.result!.exit_code !== 0);

  return (
    <div className="checks">
      <div className="summary-line">
        <Badge tone={failed.length ? 'bad' : detail.run.status === 'succeeded' ? 'ok' : 'neutral'} dot>
          {failed.length ? `${failed.length} 项未通过` : detail.active ? '正在验证' : detail.run.status === 'succeeded' ? 'Gate 已通过' : '等待结果'}
        </Badge>
        <span className="muted">{finished.length} / {checks.length} 项工具已返回 · 隔离副本中执行，业务正确性取决于测试覆盖。</span>
      </div>
      <p className="path muted">代码目录：{detail.run.data.workspace}</p>
      {checks.map((c) => {
        const r = c.result;
        const ok = r?.status === 'completed' && r.exit_code === 0;
        const diags = r && !ok ? parseDiagnostics(readableOutput(r.stdout) + '\n' + (r.stderr ?? '')) : [];
        return (
          <article className={`check ${!r ? 'run' : ok ? 'ok' : 'bad'}`} key={c.id}>
            <header>
              <h4>{NAMES[c.operation] ?? c.operation}</h4>
              <Badge tone={!r ? 'running' : ok ? 'ok' : 'bad'} dot>{!r ? '执行中' : ok ? '通过' : '未通过'}{r ? ` · ${r.duration_ms ?? 0} ms` : ''}</Badge>
            </header>
            {r && !ok && (
              <>
                <pre className="failure">{failureSummary(r) || r.error || '工具执行未完成；请查看完整输出。'}</pre>
                {diags.length > 0 && (
                  <div className="diag-links">
                    {diags.slice(0, 8).map((d, i) => (
                      <button key={i} className="chip" onClick={() => open(detail.run.id, { params: { tab: 'code', file: d.path, line: String(d.line) } })}>
                        {d.path}:{d.line}
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
            {r && (
              <details>
                <summary>完整输出 · 退出码 {r.exit_code ?? '无'}{r.truncated ? ' · 已截断' : ''}</summary>
                <pre>{readableOutput(r.stdout) || '（无标准输出）'}</pre>
                {r.stderr && <pre>{r.stderr}</pre>}
              </details>
            )}
          </article>
        );
      })}
      {!checks.length && <p className="muted">尚未执行工具。</p>}
    </div>
  );
}
