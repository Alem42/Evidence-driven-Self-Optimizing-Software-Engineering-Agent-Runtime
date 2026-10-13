import { useOpenRun } from '../../app/nav';
import { useResults } from '../../api/queries';
import type { Detail, LiveCall } from '../../api/types';
import { operationLabel } from '../../entities/languages';
import { stageLabels } from '../../entities/status';
import { failureSummary, parseDiagnostics, readableOutput } from '../../entities/text';
import { Badge, Spinner } from '../../shared/ui';
import { VerifyCard } from '../decisions/cards/VerifyCard';

// 输出：真实工具的结果，终端的样子——每项检查一行，失败的自动展开，失败线索可点击跳到编辑器里的那一行。
// Output: the real tool results, terminal-style. One line per check, failures open by themselves, and a failure hint jumps to its line in the editor.
export function OutputPanel({ detail, live }: { detail: Detail; live: LiveCall | null }) {
  const bundle = detail.run.data.project_bundle;
  const results = useResults(bundle ? detail : undefined);
  const open = useOpenRun();
  const checks = results.data?.checks ?? [];
  const finished = checks.filter((c) => c.result);
  const failed = finished.filter((c) => c.result!.status !== 'completed' || c.result!.exit_code !== 0);

  return (
    <div className="output">
      {live && live.tail && (
        <section className="live-block">
          <div className="live-head"><i className="dot tone-running pulse" /> 模型实时输出 · {stageLabels[live.step] ?? live.step}{live.model ? ' · ' + live.model : ''}</div>
          <pre className="live-tail" aria-label="模型实时输出">{live.tail}</pre>
        </section>
      )}

      {!bundle ? (
        <p className="muted pad">还没有执行工具检查。方案与代码批准后，Executor 才会运行测试、静态检查与格式检查，结果会出现在这里。</p>
      ) : results.isLoading ? (
        <div className="center pad"><Spinner /> 读取结果…</div>
      ) : (
        <>
          <div className="out-summary">
            <Badge tone={failed.length ? 'bad' : detail.run.status === 'succeeded' ? 'ok' : 'neutral'} dot>
              {failed.length ? `${failed.length} 项未通过` : detail.active ? '正在验证' : detail.run.status === 'succeeded' ? 'Gate 已通过' : '等待结果'}
            </Badge>
            <span className="muted">{finished.length} / {checks.length} 项已返回 · 在隔离副本里执行，业务正确性取决于测试覆盖</span>
          </div>
          {checks.map((c) => {
            const r = c.result;
            const ok = r?.status === 'completed' && r.exit_code === 0;
            const diags = r && !ok ? parseDiagnostics(readableOutput(r.stdout) + '\n' + (r.stderr ?? '')) : [];
            return (
              <details key={c.id} className={`term ${!r ? 'run' : ok ? 'ok' : 'bad'}`} open={Boolean(r && !ok)}>
                <summary>
                  <span className="term-mark">{!r ? '…' : ok ? '✓' : '✗'}</span>
                  <code>{operationLabel(c.operation)}</code>
                  <span className="muted">{!r ? '执行中' : `退出码 ${r.exit_code ?? '无'} · ${r.duration_ms ?? 0} ms${r.truncated ? ' · 已截断' : ''}`}</span>
                </summary>
                {r && !ok && <pre className="failure">{failureSummary(r) || r.error || '工具执行未完成；请查看完整输出。'}</pre>}
                {diags.length > 0 && (
                  <div className="diag-links">
                    {diags.slice(0, 8).map((d, i) => (
                      <button key={i} className="chip" onClick={() => open(detail.run.id, { params: { file: d.path, line: String(d.line) } })}>{d.path}:{d.line}</button>
                    ))}
                  </div>
                )}
                {r && <pre className="term-out">{readableOutput(r.stdout) || '（无标准输出）'}{r.stderr ? '\n' + r.stderr : ''}</pre>}
              </details>
            );
          })}
          {!checks.length && <p className="muted pad">尚未执行工具。</p>}
          <VerifyCard detail={detail} />
        </>
      )}
    </div>
  );
}
