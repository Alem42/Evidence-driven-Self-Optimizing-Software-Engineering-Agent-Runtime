import data from '../../data/experiments.json';
import { Badge, Card } from '../../shared/ui';
import { Bars } from './Bars';

// 实验结果：把真实评测里不同设置的影响画成表和图。数据由 scripts/eval/export_experiments.py 从账本与原始数据导出（单一来源），页面只读。
// Experiments: tables and charts of what each setting did in the real evaluations. The data is exported by scripts/eval/export_experiments.py from the ledgers and raw files.
const pct = (x: number | null | undefined) => (x == null ? '—' : Math.round(x * 100) + '%');

interface Arm { arm: string; label: string; runs: number; passed: number; cost: number; tokens: number; seconds: number }
interface Experiment { experiment: string; title: string; arms: Arm[] }

export function ExperimentsSection() {
  const ab = (data.ab as Experiment[]).filter((e) => e.arms.length > 1);
  const accuracy = data.accuracy as { method: string; all: number | null; low: number | null; mid: number | null; high: number | null }[];
  const audit = data.audit as { method: string; recall: number | null; false_alarm: number | null; fixed: number | null }[];
  const real = data.case_audit as { cases: number; suspect_original: number; corrupted: number; caught: number; cost: number } | null;
  const tasks = data.tasks as { task: string; level: number; accuracy: number | null; runs: number; passed: number; cost: number | null; rounds: number | null }[];
  const profile = data.context_profile as { roles: { role: string; calls: number; mean_tokens: number; composition: { key: string; share: number }[] }[] } | null;
  const shadow = data.context_shadow as { calls: number; windows: { window: number; large_share: number; mean_saved: number; must_items_lost: number; over_budget_calls: number }[]; projection: { files: number; without_budget: number; with_budget_for_32k_window: number }[] } | null;
  const maxTokens = Math.max(1, ...(profile?.roles.map((r) => r.mean_tokens) ?? [1]));
  return (
    <div className="stack">
      <Card title="实验结果" subtitle="真实评测（DeepSeek Pro + Flash）里不同设置的影响。样本都很小，数字是方向不是结论；每张图下面写了怎么读。" />

      <Card title="1 · 模型当“测试作者”：期望值推导的准确率" subtitle="对 23 个任务的固定输入，让模型推导期望输出，与独立的参考实现逐用例比对。">
        <Bars rows={accuracy.map((r) => ({ label: r.method, value: r.all, tone: r.method.includes('表决') ? 'ok' : 'muted' }))} />
        <p className="hint">便宜的多数表决（Flash×3，92%）胜过单次的 Pro（76%）；Pro 单次并不比 Flash 单次准。表决时没有严格多数的用例弃权。</p>
        <div className="table-wrap"><table>
          <thead><tr><th>方法</th><th>全部</th><th>L0–L3</th><th>L4–L6</th><th>L7–L9</th></tr></thead>
          <tbody>{accuracy.map((r) => <tr key={r.method}><td>{r.method}</td><td>{pct(r.all)}</td><td>{pct(r.low)}</td><td>{pct(r.mid)}</td><td>{pct(r.high)}</td></tr>)}</tbody>
        </table></div>
      </Card>

      <Card title="2 · 能抓到被故意改错的期望吗" subtitle="把正确的期望一半原样、一半故意改错，看各种方法的召回（越高越好）与误报（越低越好）。">
        <Bars rows={audit.map((r) => ({ label: r.method, value: r.recall, tone: r.method.startsWith('盲推导') ? 'ok' : 'muted', note: `误报 ${pct(r.false_alarm)}` }))} />
        <p className="hint">直接问“这个期望对不对”召回只有 26–33%（被声明的期望锚定）；让模型<strong>看不到</strong>声明的期望、独立推导再机械比较，召回 91%、误报 8%（Flash×3 表决）。</p>
        {real && (
          <div className="tiles">
            <div className="tile"><strong>{Math.round((100 * real.caught) / real.corrupted)}%</strong><span>真实 Tester 用例里被改错的期望抓到（{real.caught}/{real.corrupted}）</span></div>
            <div className="tile"><strong>{Math.round((100 * real.suspect_original) / real.cases)}%</strong><span>原样用例被判可疑（{real.suspect_original}/{real.cases}，含 Tester 自己的错误，是误报上界）</span></div>
            <div className="tile"><strong>¥{real.cost.toFixed(2)}</strong><span>这次审计实验的花费</span></div>
          </div>
        )}
      </Card>

      <Card title="3 · 不同设置的配对评测（同一任务，关 vs 开）" subtitle="通过率与每次费用。每组 1–14 次，噪声大，结论见各自的进展文档。">
        {ab.map((e) => (
          <div key={e.experiment} className="stack">
            <h4>{e.title}</h4>
            <Bars rows={e.arms.map((a) => ({ label: `${a.label}（${a.passed}/${a.runs}）`, value: a.runs ? a.passed / a.runs : null, tone: a.arm === 'off' ? 'muted' : 'accent', note: `¥${a.cost.toFixed(2)}/次` }))} />
          </div>
        ))}
        <p className="hint">指挥者与 best-of-N 没有看到通过率收益、反而更贵，所以默认关闭。“Tester 用例审计（三轮合计）”是目前唯一看到正面信号的：17 个有效配对里 5 个更好、0 个更差、12 个持平，通过 16/21 对 11/20，平均费用 −36%；仍是小样本，默认关闭，可在“路由与预算”里开启。经验库在留出任务上没有产生任何命中，所以没有可测的效果。</p>
      </Card>

      <Card title="4 · 任务的测试难度与成本" subtitle="难度 = 1 − Pro 单次期望准确率；通过/费用来自默认流程的真实运行。">
        <div className="table-wrap"><table>
          <thead><tr><th>任务</th><th>等级</th><th>期望准确率</th><th>通过</th><th>平均轮数</th><th>平均费用</th></tr></thead>
          <tbody>{tasks.map((t) => (
            <tr key={t.task}><td>{t.task}</td><td>L{t.level}</td>
              <td><span className="heat" style={{ background: `rgba(${t.accuracy != null && t.accuracy < 0.7 ? '220,80,60' : '60,170,110'},${t.accuracy == null ? 0 : 0.18 + 0.25 * (t.accuracy < 0.7 ? 1 - t.accuracy : t.accuracy)})` }}>{pct(t.accuracy)}</span></td>
              <td>{t.runs ? `${t.passed}/${t.runs}` : '—'}</td><td>{t.rounds ?? '—'}</td><td>{t.cost == null ? '—' : '¥' + t.cost.toFixed(3)}</td></tr>
          ))}</tbody>
        </table></div>
      </Card>

      {profile && (
        <Card title="5 · 上下文：每个角色的输入有多大" subtitle={`来自真实账本的角色输入（估算 token）。修复与修订测试的输入最大，主要是失败证据和文件。`}>
          <Bars percent={false} max={maxTokens} rows={profile.roles.map((r) => ({ label: r.role, value: r.mean_tokens, tone: r.mean_tokens > 5000 ? 'bad' : 'accent', note: r.composition.slice(0, 2).map((c) => `${c.key} ${Math.round(c.share * 100)}%`).join(' · ') }))} />
        </Card>
      )}

      {shadow && (
        <Card title="6 · 上下文预算的影子重放" subtitle={`用 ${shadow.calls} 次真实的修复输入，假设窗口变小（影子模式，不改变行为）：必需内容从不被丢。`}>
          <div className="table-wrap"><table>
            <thead><tr><th>假设的窗口</th><th>被判为大任务</th><th>平均省下 token</th><th>丢掉的必需项</th><th>必需项就超预算</th></tr></thead>
            <tbody>{shadow.windows.map((w) => <tr key={w.window}><td>{w.window}</td><td>{pct(w.large_share)}</td><td>{w.mean_saved}</td><td>{w.must_items_lost}</td><td>{w.over_budget_calls}</td></tr>)}</tbody>
          </table></div>
          <h4>项目变大时修复输入的外推</h4>
          <div className="table-wrap"><table>
            <thead><tr><th>文件数</th><th>不做预算</th><th>做预算（32k 窗口）</th></tr></thead>
            <tbody>{shadow.projection.map((p) => <tr key={p.files}><td>{p.files}</td><td>{p.without_budget}</td><td>{p.with_budget_for_32k_window}</td></tr>)}</tbody>
          </table></div>
          <Badge tone="neutral">小任务原样全给，只有大任务才按层裁剪</Badge>
        </Card>
      )}
    </div>
  );
}
