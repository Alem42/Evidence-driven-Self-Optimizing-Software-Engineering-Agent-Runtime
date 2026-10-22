import { useState } from 'react';
import { useCallDetail } from '../../api/queries';
import type { CallCategory, CallDetail, CallSection } from '../../api/types';
import { fmtDuration, fmtTokens } from '../../entities/report';
import { stageLabels } from '../../entities/status';
import { Spinner } from '../../shared/ui';
import { fmtRate } from './Chips';

/** 每个类别一个固定的颜色：同一类别在不同调用里颜色一致，才能横向比较。 One fixed colour per category so the same category looks the same in every call. */
export const CATEGORY_COLOR: Record<string, string> = {
  system: '#4f8ef7',
  task: '#e9a23b',
  tests: '#3fb27f',
  code: '#9b72e8',
  memory: '#e86aa8',
  evidence: '#e5594f',
  conventions: '#2fb3b3',
  human: '#f08a3c',
  control: '#8d96a8',
  other: '#6b7280',
  output: '#7d8aa6',
};

const pct = (n: number | null | undefined): string => (n == null ? '—' : n > 0 && n * 100 < 0.1 ? '<0.1%' : n * 100 < 10 ? (n * 100).toFixed(1) + '%' : Math.round(n * 100) + '%');
const signed = (n: number): string => (n === 0 ? '' : (n > 0 ? '+' : '−') + fmtTokens(Math.abs(n)));

/** 窗口占用：已用 / 窗口，一条横条（已用、输出预留、剩余），上方是醒目的百分比，像 Claude Code 的“上下文”面板。 Window usage: a headline percentage and one bar (used, output reserve, free), like Claude Code's context panel. */
function WindowUsage({ d }: { d: CallDetail }) {
  const w = d.window;
  const limit = w.limit;
  return (
    <section className="ctx-card">
      <header className="ctx-title">上下文窗口 <span>Context window</span></header>
      {limit ? (
        <>
          <div className="ctx-headline">
            <b>已使用 {pct(w.percent)}</b>
            <span>{w.used.toLocaleString()} / {fmtTokens(limit)} tok{w.source === 'estimate' ? '（本地估算）' : '（服务端计）'}</span>
          </div>
          <div className="ctx-window-bar" role="img" aria-label="窗口占用">
            <i className="used" style={{ width: Math.max(0.6, (w.used / limit) * 100) + '%' }} />
            {w.output_reserve ? <i className="reserve" style={{ width: (w.output_reserve / limit) * 100 + '%' }} title={`输出预留 ${w.output_reserve} tok`} /> : null}
          </div>
          <div className="ctx-foot">
            <span>输出预留 {w.output_reserve ? fmtTokens(w.output_reserve) : '—'}</span>
            <span>剩余 {fmtTokens(w.free)}</span>
          </div>
        </>
      ) : (
        <p className="ctx-note">这个模型没有配置上下文长度，无法给出窗口占用（设置 → 模型配置里可以填）。提示共 {w.used.toLocaleString()} tok（{w.source === 'server' ? '服务端计' : '本地估算'}）。</p>
      )}
    </section>
  );
}

function FieldRow({ s }: { s: CallSection }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <li className="ctx-field" onClick={() => setOpen(!open)}>
        <code>{s.name}</code>
        {s.name === 'user_notes' && <span className="tag tag-accent">你的提示</span>}
        <span className="ctx-field-size">{s.chars.toLocaleString()} 字符 · ≈ {fmtTokens(s.est_tokens)} tok</span>
        <span className="caret">{open ? '▾' : '▸'}</span>
      </li>
      {open && (
        <li className="ctx-field-open">
          {s.entries.length > 0 && (
            <ul className="entries">
              {s.entries.map((e) => <li key={e.name}><code>{e.name}</code><span>{e.chars.toLocaleString()} 字符 · ≈ {fmtTokens(e.est_tokens)} tok</span></li>)}
            </ul>
          )}
          <pre className="preview">{s.preview}{s.chars > s.preview.length ? '\n…' : ''}</pre>
        </li>
      )}
    </>
  );
}

/** 提示词构成：按类别的堆叠条 + 清单（类别、token、占比、相对上一次调用的变化）；点一个类别展开它的字段，字段里还能看到每个文件各占多少。 Prompt composition: a stacked bar and a list per category, expandable down to fields and files. */
function Composition({ d }: { d: CallDetail }) {
  const [open, setOpen] = useState<string | null>(null);
  const total = d.categories.reduce((n, c) => n + c.tokens, 0) || 1;
  const sectionsOf = (c: CallCategory): CallSection[] => d.sections.filter((s) => c.sections.includes(s.name));
  const delta = d.delta_vs_previous;
  return (
    <section className="ctx-card">
      <header className="ctx-title">提示词构成 <span>Prompt composition</span><em>≈ {fmtTokens(total)} tok（本地估算）</em></header>
      <div className="ctx-bar" role="img" aria-label="提示词各类别占比">
        {d.categories.filter((c) => c.tokens > 0).map((c) => <i key={c.id} style={{ width: (c.tokens / total) * 100 + '%', background: CATEGORY_COLOR[c.id] }} title={`${c.label} ≈ ${c.tokens} tok`} />)}
      </div>
      <ul className="ctx-list">
        {d.categories.map((c) => {
          const fields = sectionsOf(c);
          const expandable = fields.length > 0;
          const change = delta?.by_category[c.id] ?? 0;
          return (
            <li key={c.id} className={c.tokens === 0 ? 'empty' : ''}>
              <div className={`ctx-row ${expandable ? 'click' : ''}`} onClick={() => expandable && setOpen(open === c.id ? null : c.id)}>
                <i className="ctx-swatch" style={{ background: CATEGORY_COLOR[c.id] }} />
                <span className="ctx-name">{c.label}<small>{c.english}</small></span>
                <span className="ctx-delta" title={delta ? `相对同角色上一次调用（${delta.against}）` : ''}>{signed(change)}</span>
                <span className="ctx-tokens">{c.tokens > 0 ? fmtTokens(c.tokens) : '—'}</span>
                <span className="ctx-share">{c.tokens > 0 ? pct(c.share) : ''}</span>
                <span className="caret">{expandable ? (open === c.id ? '▾' : '▸') : ''}</span>
              </div>
              {open === c.id && <ul className="ctx-fields">{fields.map((s) => <FieldRow key={s.name} s={s} />)}</ul>}
            </li>
          );
        })}
        <li className="ctx-row reserve-row">
          <i className="ctx-swatch hatch" style={{ borderColor: CATEGORY_COLOR.output }} />
          <span className="ctx-name">输出预留<small>Reserved for output (max_tokens)</small></span>
          <span className="ctx-delta" />
          <span className="ctx-tokens">{d.window.output_reserve ? fmtTokens(d.window.output_reserve) : '—'}</span>
          <span className="ctx-share" />
          <span className="caret" />
        </li>
        <li className="ctx-row free-row">
          <i className="ctx-swatch outline" />
          <span className="ctx-name">剩余空间<small>Free space</small></span>
          <span className="ctx-delta" />
          <span className="ctx-tokens">{d.window.free != null ? fmtTokens(d.window.free) : '—'}</span>
          <span className="ctx-share">{d.window.limit && d.window.free != null ? pct(d.window.free / d.window.limit) : ''}</span>
          <span className="caret" />
        </li>
      </ul>
      {delta && <p className="ctx-note">与同角色上一次调用（<code>{delta.against}</code>）相比，提示共 {delta.total === 0 ? '没有变化' : (delta.total > 0 ? '增加 ' : '减少 ') + fmtTokens(Math.abs(delta.total)) + ' tok'}。</p>}
    </section>
  );
}

const STRATEGY_TEXT: Record<string, string> = {
  window: 'window · 最近 3 步原文（原有行为）', digest_wide: 'digest_wide · 大窗口，原文 5 步 + 摘要行 + 钉住项', digest: 'digest · 原文 3 步 + 摘要行 + 钉住项',
  digest_tight: 'digest_tight · 小窗口，原文 1 步 + 更短的摘要行',
};
const PIN_TEXT: Record<string, string> = { failing_check: '当前失败的检查', referee_verdicts: '裁判结论', same_failure_repeats: '同一失败重复次数', best_problems_so_far: '最好状态的问题数' };

/** 工作记忆：最近结果窗口保留几条、更早的几条已经离开窗口、有没有笔记/摘要。离开窗口的结果不会自动并入笔记——没有笔记时标红。 Working memory: what the recent-results window keeps and what left it; results that leave are not folded into notes automatically, so no notes is flagged. */
function WorkingMemory({ d }: { d: CallDetail }) {
  const m = d.working_memory;
  if (!m) return null;
  return (
    <section className="ctx-card">
      <header className="ctx-title">工作记忆 <span>Working memory</span><em>{m.step != null ? `第 ${m.step}${m.max_steps ? ' / ' + m.max_steps : ''} 步` : ''}</em></header>
      <div className="ctx-kv">
        <span>最近结果窗口</span><b>保留 {m.kept} 条</b>
        <span>已离开窗口</span><b className={m.at_risk ? 'bad-text' : ''}>{m.dropped == null ? '—' : m.dropped + ' 条'}</b>
        <span>笔记</span><b>{m.notes_chars ? m.notes_chars.toLocaleString() + ' 字符' : '无'}</b>
        <span>记忆策略</span><b>{STRATEGY_TEXT[m.strategy] ?? m.strategy}</b>
        <span>摘要行</span><b>{m.digest_lines ? m.digest_lines + ' 行（每个离开窗口的结果一行）' : m.strategy === 'window' ? '没有（window 策略）' : '无（还没有结果离开窗口）'}</b>
        <span>钉住项</span><b>{m.pins.length ? m.pins.map((p) => PIN_TEXT[p] ?? p).join('、') : '无'}</b>
        <span>回查旧结果</span><b>{m.recall ? '可用（recall_step）' : '不可用'}</b>
        <span>LLM 滚动摘要</span><b>{m.summary_chars ? m.summary_chars.toLocaleString() + ' 字符' : '没有'}</b>
      </div>
      {m.at_risk && <p className="ctx-warn">更早的 {m.dropped} 条结果已经离开窗口，而且没有笔记、摘要行或钉住项：其中的信息现在只存在于文件内容里，模型可能已经忘了。</p>}
      {m.kept_items.length > 0 && (
        <ul className="entries">
          {m.kept_items.map((r, i) => <li key={i}><code>{r.tool ?? '（被拒绝的回答）'}{r.path ? ' ' + r.path : ''}</code><span>{r.ok === false ? '失败 · ' : ''}{r.chars.toLocaleString()} 字符</span></li>)}
        </ul>
      )}
    </section>
  );
}

// 调用检视：模型这一次到底看到了什么——像 Claude Code 的“上下文”面板：窗口占用、按类别的构成、工作记忆、缓存、被省略的内容、你的提示、模型的回答。
// Call inspector: what the model saw this time, laid out like Claude Code's context panel: window usage, composition by category, working memory, cache, omissions, your notes, the answer.
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

      <WindowUsage d={d} />
      <Composition d={d} />
      <WorkingMemory d={d} />

      {d.prefix_with_previous && (
        <section className="ctx-card">
          <header className="ctx-title">缓存前缀 <span>Prefix cache</span><em>排布 {d.layout === 'stable_first' ? '稳定内容在前' : '字母序（旧）'}</em></header>
          <div className="ctx-headline">
            <b>可复用前缀 {d.prefix_with_previous.share != null ? pct(d.prefix_with_previous.share) : '—'}</b>
            <span>{d.prefix_with_previous.common_chars.toLocaleString()} / {d.prefix_with_previous.of_chars.toLocaleString()} 字符</span>
          </div>
          <div className="ctx-window-bar"><i className="used cache" style={{ width: (d.prefix_with_previous.share ?? 0) * 100 + '%' }} /></div>
          <p className="ctx-note">与同角色上一次调用（<code>{d.prefix_with_previous.against.invocation}</code>）开头相同的部分。服务端按“相同的开头”缓存，所以不变的内容放前面、会变的放后面才有用。</p>
        </section>
      )}

      {d.user_notes.length > 0 && (
        <section className="ctx-card">
          <header className="ctx-title">人工输入 <span>Human notes</span><em>已带进这次调用</em></header>
          <ul className="notes-list">{d.user_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      )}

      {(d.omitted.length > 0 || d.context_plans.length > 0) && (
        <section className="ctx-card">
          <header className="ctx-title">被省略的内容 <span>Omitted</span></header>
          <ul className="omitted">
            {d.omitted.map((o, i) => <li key={i}><code>{o.id}</code><span>{o.reason || '未说明'}</span></li>)}
            {d.context_plans.map((p, i) => (
              <li key={'p' + i}>
                <code>上下文预算（{p.where}）</code>
                <span>窗口 {fmtTokens(p.window)} · 需要 {fmtTokens(p.total)} → 保留 {fmtTokens(p.kept_tokens)}；降为轮廓 {p.outlined.length} 个、丢弃 {p.dropped.length} 个{p.over_budget ? ' · 必需内容仍超出预算' : ''}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {d.response && (
        <section className="ctx-card">
          <header className="ctx-title">模型的回答 <span>Response</span><em>{d.response.chars.toLocaleString()} 字符{d.response.keys ? ' · ' + d.response.keys.join('、') : ''}</em></header>
          <pre className="preview">{d.response.preview}{d.response.chars > d.response.preview.length ? '\n…' : ''}</pre>
        </section>
      )}
      {d.error && <p className="bad-text">{d.error}</p>}
    </div>
  );
}
