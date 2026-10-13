import { useState } from 'react';
import { useCallDetail } from '../../api/queries';
import type { CallDetail, CallSection } from '../../api/types';
import { fmtDuration, fmtTokens } from '../../entities/report';
import { stageLabels } from '../../entities/status';
import { Spinner } from '../../shared/ui';
import { fmtRate } from './Chips';

const hue = (i: number): string => `hsl(${(i * 53 + 210) % 360} 52% 56%)`;
const pct = (n: number): string => (n * 100 < 1 && n > 0 ? '<1%' : Math.round(n * 100) + '%');

/** 上下文各部分占多少：一条堆叠条 + 图例（系统指令也算一段）。 What each part of the prompt costs: one stacked bar and a legend (the system instruction counts as a part). */
function Composition({ d }: { d: CallDetail }) {
  const parts = [{ name: '系统指令', est_tokens: d.instruction.est_tokens }, ...d.sections.map((s) => ({ name: s.name, est_tokens: s.est_tokens }))];
  const total = parts.reduce((n, p) => n + p.est_tokens, 0) || 1;
  return (
    <>
      <div className="comp-bar" role="img" aria-label="提示各部分占比">
        {parts.map((p, i) => <i key={p.name} style={{ width: (p.est_tokens / total) * 100 + '%', background: hue(i) }} title={`${p.name} ≈ ${p.est_tokens} tok`} />)}
      </div>
      <div className="comp-legend">
        {parts.filter((p) => p.est_tokens / total >= 0.02).map((p) => {
          const i = parts.indexOf(p);
          return <span key={p.name}><i style={{ background: hue(i) }} />{p.name} {pct(p.est_tokens / total)}</span>;
        })}
      </div>
    </>
  );
}

function SectionRow({ s, index }: { s: CallSection; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="sec-row" onClick={() => setOpen(!open)}>
        <td><i className="sec-swatch" style={{ background: hue(index + 1) }} /> {s.name}{s.name === 'user_notes' && <span className="tag tag-accent">你的提示</span>}</td>
        <td className="num">≈ {fmtTokens(s.est_tokens)}</td>
        <td className="num">{pct(s.share)}</td>
        <td className="num">{s.chars.toLocaleString()}</td>
        <td className="caret">{open ? '▾' : '▸'}</td>
      </tr>
      {open && (
        <tr className="sec-detail">
          <td colSpan={5}>
            {s.entries.length > 0 && (
              <ul className="entries">
                {s.entries.map((e) => <li key={e.name}><code>{e.name}</code><span>{e.chars.toLocaleString()} 字符 · ≈ {fmtTokens(e.est_tokens)} tok</span></li>)}
              </ul>
            )}
            <pre className="preview">{s.preview}{s.chars > s.preview.length ? '\n…' : ''}</pre>
          </td>
        </tr>
      )}
    </>
  );
}

// 调用检视：模型这一次到底看到了什么——各部分占多少、哪些被省略以及原因、你的提示有没有带上、服务端缓存命中了多少。
// Call inspector: what the model actually saw this time: size of each part, what was left out and why, whether your note was included, how much the server cached.
export function CallInspector({ runId, step, invocation }: { runId: string; step: string; invocation: string }) {
  const q = useCallDetail(runId, step, invocation);
  if (q.isLoading) return <div className="center pad"><Spinner /> 读取这次调用…</div>;
  if (q.isError || !q.data) return <p className="bad-text pad">{(q.error as Error | null)?.message ?? '读取失败'}</p>;
  const d = q.data;
  const u = d.usage;
  const seen = (u.cache_hit_tokens ?? 0) + (u.cache_miss_tokens ?? 0);
  const rate = u.cache_hit_tokens != null && seen ? u.cache_hit_tokens / seen : null;

  return (
    <div className="inspect">
      <header className="inspect-head">
        <strong>{stageLabels[d.step] ?? d.step}</strong>
        <code>{d.invocation}</code>
        <span className="muted">第 {d.attempt} 次 · {d.model ?? '未知模型'} · {fmtDuration(d.duration_ms)} · {d.status}</span>
      </header>

      <div className="stat-line">
        <span>提示 <b>{u.prompt_tokens != null ? fmtTokens(u.prompt_tokens) : '—'}</b> tok<small>（服务端计；本地估算 ≈ {fmtTokens(d.est_total_tokens)}）</small></span>
        <span>输出 <b>{u.completion_tokens != null ? fmtTokens(u.completion_tokens) : '—'}</b> tok</span>
        <span>缓存命中 <b>{fmtRate(rate)}</b><small>{u.cache_hit_tokens != null ? `（命中 ${fmtTokens(u.cache_hit_tokens)} · 未命中 ${fmtTokens(u.cache_miss_tokens)}）` : '（服务端没有报告）'}</small></span>
      </div>

      <h4>模型看到了什么 <span className="muted">· 排布 {d.layout === 'stable_first' ? '稳定内容在前' : '字母序（旧）'}</span></h4>
      <Composition d={d} />
      <table className="sections">
        <thead><tr><th>部分</th><th>token（估算）</th><th>占比</th><th>字符</th><th /></tr></thead>
        <tbody>
          <tr className="sec-row static"><td><i className="sec-swatch" style={{ background: hue(0) }} /> 系统指令</td><td className="num">≈ {fmtTokens(d.instruction.est_tokens)}</td><td /><td className="num">{d.instruction.chars.toLocaleString()}</td><td /></tr>
          {d.sections.map((s, i) => <SectionRow key={s.name} s={s} index={i} />)}
        </tbody>
      </table>

      {d.prefix_with_previous && (
        <>
          <h4>可被缓存复用的前缀</h4>
          <p className="inspect-note">
            与同角色上一次调用（<code>{d.prefix_with_previous.against.invocation}</code>）的开头相同的部分有 {d.prefix_with_previous.common_chars.toLocaleString()} 字符，占这次提示的 <b>{d.prefix_with_previous.share != null ? pct(d.prefix_with_previous.share) : '—'}</b>。
            服务端按“相同的开头”缓存，所以不变的内容放前面、会变的放后面才有用。
          </p>
        </>
      )}

      {d.user_notes.length > 0 && (
        <>
          <h4>你的提示（已带进这次调用）</h4>
          <ul className="notes-list">{d.user_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </>
      )}

      {(d.omitted.length > 0 || d.context_plans.length > 0) && (
        <>
          <h4>被省略的内容与原因</h4>
          <ul className="omitted">
            {d.omitted.map((o, i) => <li key={i}><code>{o.id}</code><span>{o.reason || '未说明'}</span></li>)}
            {d.context_plans.map((p, i) => (
              <li key={'p' + i}>
                <code>上下文预算（{p.where}）</code>
                <span>窗口 {fmtTokens(p.window)} · 需要 {fmtTokens(p.total)} → 保留 {fmtTokens(p.kept_tokens)}；降为轮廓 {p.outlined.length} 个、丢弃 {p.dropped.length} 个{p.over_budget ? ' · 必需内容仍超出预算' : ''}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {d.response && (
        <>
          <h4>模型的回答 <span className="muted">· {d.response.chars.toLocaleString()} 字符{d.response.keys ? ' · ' + d.response.keys.join('、') : ''}</span></h4>
          <pre className="preview">{d.response.preview}{d.response.chars > d.response.preview.length ? '\n…' : ''}</pre>
        </>
      )}
      {d.error && <p className="bad-text">{d.error}</p>}
    </div>
  );
}
