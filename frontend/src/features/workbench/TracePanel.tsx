import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCalls } from '../../api/queries';
import type { CallRow, Detail, ProjectView } from '../../api/types';
import { fmtDuration, fmtTokens } from '../../entities/report';
import { stageLabels } from '../../entities/status';
import { Spinner } from '../../shared/ui';
import { ActivityLog } from '../decisions/ActivityLog';
import { CallInspector } from './CallInspector';
import { fmtRate } from './Chips';

const key = (c: CallRow): string => c.run_id + '|' + c.step + '|' + c.invocation;
const cacheRate = (c: CallRow): number | null => {
  const seen = (c.cache_hit_tokens ?? 0) + (c.cache_miss_tokens ?? 0);
  return c.cache_hit_tokens != null && seen ? c.cache_hit_tokens / seen : null;
};

// 追踪：整条版本链上的每一次模型调用（旧→新），点一行就能看“模型看到了什么”。点流程带里的阶段，会把这个角色的调用高亮出来。
// Trace: every model call along the version chain, oldest first; click a row to see what the model saw. Clicking a stage in the flow strip highlights that role's calls.
export function TracePanel({ detail, view, live }: { detail: Detail; view?: ProjectView; live: boolean }) {
  const calls = useCalls(detail.run.id, true, live);
  const [params] = useSearchParams();
  const [picked, setPicked] = useState<string>();
  const role = view?.stages.find((s) => s.id === params.get('node'))?.role;
  const rows = calls.data?.calls ?? [];
  const current = useMemo(() => rows.find((c) => key(c) === picked) ?? rows[rows.length - 1], [rows, picked]);

  // 点了流程带里的阶段：选中这个角色最近的一次调用。 A stage was clicked: select that role's latest call.
  useEffect(() => {
    if (!role) return;
    const last = [...rows].reverse().find((c) => c.step === role);
    if (last) setPicked(key(last));
  }, [role, rows.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (calls.isLoading) return <div className="center pad"><Spinner /> 读取调用记录…</div>;
  if (!rows.length) return <p className="muted pad">还没有模型调用。第一次调用开始后，这里会出现一行，点开就能看到模型看到了什么。</p>;
  const t0 = rows[0].started ?? 0;

  return (
    <div className="trace">
      <div className="trace-list">
        <table className="calls-table">
          <thead><tr><th>#</th><th>角色</th><th>模型</th><th className="num">提示</th><th className="num">缓存</th><th className="num">耗时</th></tr></thead>
          <tbody>
            {rows.map((c, i) => (
              <tr key={key(c)} className={`${current && key(c) === key(current) ? 'on' : ''} ${role && c.step === role ? 'role-hit' : ''} s-${c.status}`} onClick={() => setPicked(key(c))}>
                <td>{i + 1}</td>
                <td>
                  <i className={`dot ${c.status === 'failed' ? 'tone-bad' : c.status === 'pending' ? 'tone-running pulse' : 'tone-ok'}`} />
                  {stageLabels[c.step] ?? c.step}
                  {c.invocation !== 'initial' && <small> {c.invocation}</small>}
                  {c.notes > 0 && <span className="tag tag-accent" title="这次调用带着你追加的提示">✎{c.notes}</span>}
                </td>
                <td>{c.model}</td>
                <td className="num">{fmtTokens(c.prompt_tokens)}</td>
                <td className="num">{fmtRate(cacheRate(c))}</td>
                <td className="num" title={c.started ? '开始于 +' + fmtDuration(((c.started ?? 0) - t0) * 1000) : ''}>{fmtDuration(c.duration_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <ActivityLog detail={detail} />
      </div>
      <div className="trace-detail">{current ? <CallInspector key={key(current)} runId={current.run_id} step={current.step} invocation={current.invocation} /> : null}</div>
    </div>
  );
}
