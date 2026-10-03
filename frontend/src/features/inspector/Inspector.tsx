import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useArtifact } from '../../api/queries';
import type { Detail, ProjectView, Stage } from '../../api/types';
import { useOpenRun } from '../../app/nav';
import { statusText, stageLabels } from '../../entities/status';
import { fmtSeconds } from '../../entities/text';
import { Badge, Button } from '../../shared/ui';

const toneOf = (s: string) => (s === 'succeeded' ? 'ok' : s === 'failed' ? 'bad' : s === 'running' ? 'running' : s === 'blocked' ? 'wait' : 'neutral');

function RawResponse({ runId, refId }: { runId: string; refId: string }) {
  const [show, setShow] = useState(false);
  const art = useArtifact(runId, show ? refId : undefined);
  return (
    <div>
      <Button size="sm" variant="ghost" onClick={() => setShow(!show)}>{show ? '收起原始响应' : '查看原始响应'}</Button>
      {show && <pre className="raw">{art.isLoading ? '读取中…' : JSON.stringify(art.data, null, 2)}</pre>}
    </div>
  );
}

// Inspector：选中节点的事实——角色调用、模型事件、产物与跳转。以后模型/升级/预算信息放在这里扩展。
// Inspector: facts about the selected node. Model routing/escalation/budget details extend here later.
export function Inspector({ detail, view }: { detail: Detail; view?: ProjectView }) {
  const [params] = useSearchParams();
  const open = useOpenRun();
  const stage: Stage | undefined = view?.stages.find((s) => s.id === params.get('node'));
  const t0 = detail.run.data.created_at ?? 0;

  if (!stage)
    return (
      <aside className="inspector" aria-label="详情">
        <h2>任务详情</h2>
        <p className="muted">在上方图中点击一个节点，查看该角色的调用、耗时与产物。</p>
        <dl className="facts">
          <dt>版本</dt><dd><code>{detail.run.id.slice(0, 8)}</code></dd>
          <dt>状态</dt><dd>{statusText[detail.run.status] ?? detail.run.status}</dd>
          <dt>事件数</dt><dd>{detail.event_count}</dd>
          <dt>代码目录</dt><dd className="path">{detail.run.data.workspace || '—'}</dd>
        </dl>
        <h3>角色调用</h3>
        {detail.role_calls.length ? (
          <ul className="calls">
            {detail.role_calls.map((c) => (
              <li key={c.purpose + c.invocation_id}>
                <strong>{stageLabels[c.purpose] ?? c.purpose}</strong>
                <span className="muted">第 {c.attempt_no ?? 1} 次 · {c.status} · {c.output_ref ? '结果已保存' : '结果未保存'}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">暂无角色调用。</p>
        )}
      </aside>
    );

  const calls = detail.role_calls.filter((c) => c.purpose === stage.role);
  const events = detail.events.filter((e) => e.payload?.step_id === stage.role && e.type.startsWith('model_'));
  const tab = stage.role === 'executor' || stage.role === 'gate' ? 'checks' : stage.role === 'project_developer' || stage.role === 'project_repair' || stage.role === 'project_test_revision' ? 'code' : 'thread';

  return (
    <aside className="inspector" aria-label="节点详情">
      <h2>{stage.label}</h2>
      <div className="row"><Badge tone={toneOf(stage.status)} dot>{statusText[stage.status] ?? stage.status}</Badge><span className="chip-static">{stage.kind}</span></div>
      <dl className="facts">
        <dt>所属版本</dt>
        <dd><button className="link" onClick={() => open(stage.run_id, { manual: true })}>{stage.run_id.slice(0, 8)}</button></dd>
        {stage.reused && (<><dt>说明</dt><dd>复用已有结果，未重新调用模型</dd></>)}
      </dl>
      <Button size="sm" onClick={() => open(stage.run_id, { tab, params: { node: stage.id } })}>跳到相关内容 →</Button>

      {events.length > 0 && (
        <>
          <h3>模型调用</h3>
          <ul className="calls">
            {events.map((e) => (
              <li key={e.seq}>
                <strong>{e.type === 'model_requested' ? '发起请求' : e.type === 'model_completed' ? '响应完成' : '调用失败'}</strong>
                <span className="muted">
                  +{fmtSeconds(Math.max(0, e.created - t0))}
                  {e.payload.metrics?.generation_tokens_per_second != null && ` · ${e.payload.metrics.generation_tokens_per_second} tok/s`}
                  {e.payload.metrics?.eval_count != null && ` · 输出 ${e.payload.metrics.eval_count} tok`}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {calls.length > 0 && (
        <>
          <h3>角色账本</h3>
          <ul className="calls">
            {calls.map((c) => (
              <li key={c.invocation_id}><strong>第 {c.attempt_no ?? 1} 次</strong><span className="muted">{c.status} · {c.output_ref ? '结果已保存' : '结果未保存'}</span></li>
            ))}
          </ul>
        </>
      )}
      {stage.checks && (
        <>
          <h3>检查步骤</h3>
          <ul className="calls">
            {stage.checks.map((c) => (<li key={c.id}><strong>{c.id}</strong><span className="muted">{statusText[c.status] ?? c.status}</span></li>))}
          </ul>
        </>
      )}
      {stage.response_ref && <RawResponse runId={stage.run_id} refId={stage.response_ref} />}
    </aside>
  );
}
