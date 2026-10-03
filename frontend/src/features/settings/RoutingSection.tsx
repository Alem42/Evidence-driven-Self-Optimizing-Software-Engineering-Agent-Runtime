import { useActivity } from '../../app/activity';
import { useProfiles } from '../../api/queries';
import { byRouting, profileLabel, profileLevel, profileReady } from '../../entities/profiles';
import { Badge, Card, Notice } from '../../shared/ui';



// 路由与预算：展示候选顺序与默认预算/策略（M0 只读；可编辑在 M0.5）。 Read-only in M0; editing planned for M0.5.
export function RoutingSection() {
  const { data } = useProfiles();
  const defaults = useActivity().bootstrap?.routing_defaults;
  const list = [...(data?.profiles ?? [])].sort(byRouting);
  return (
    <div className="stack">
      <Notice tone="ok">M0 已实现：新建任务时选择「本地优先 · 有界升级」（需自动执行）即启用路由与任务级预算；固定模型仍是默认行为。Diagnoser、流式、动态角色在后续里程碑。</Notice>
      <Card title="候选模型（等级 ↑ 优先级 ↑）" subtitle="未来策略：本地优先 → 当前模型自修一次 → 升级到更高等级 → 受预算约束。">
        <ol className="route-list">
          {list.map((p) => (
            <li key={p.id}>
              <Badge tone={p.model_type === 'local' ? 'ok' : 'running'}>L{profileLevel(p)}</Badge>
              <strong>{profileLabel(p)}</strong>
              <span className="muted">{p.model} · 优先级 {p.priority ?? 0} · 上下文 {p.context_limit ?? '—'}</span>
              <Badge tone={profileReady(p) ? 'ok' : 'neutral'}>{profileReady(p) ? '可用' : '不可用'}</Badge>
            </li>
          ))}
          {!list.length && <p className="muted">还没有模型配置。</p>}
        </ol>
      </Card>
      <Card title="默认预算与策略" subtitle="新任务可覆盖 API token 上限；其余沿用默认。">
        {defaults ? (
          <dl className="facts">
            <dt>模型调用上限</dt><dd>{defaults.budget.max_model_calls ?? '不限'}</dd>
            <dt>API token 上限</dt><dd>{defaults.budget.max_cloud_tokens ?? '不限'}</dd>
            <dt>运行时间上限</dt><dd>{defaults.budget.max_active_seconds != null ? defaults.budget.max_active_seconds + ' 秒（不含等待你确认）' : '不限'}</dd>
            <dt>费用上限</dt><dd>{defaults.budget.max_cost ?? '不限（需配置价格才能启用）'}</dd>
            <dt>每级尝试次数</dt><dd>规划 {defaults.policy.attempts_per_level.planning} · 生成 {defaults.policy.attempts_per_level.generation} · 修复 {defaults.policy.attempts_per_level.fix}（最高等级不限，受总轮次约束）</dd>
            <dt>最多升级</dt><dd>{defaults.policy.max_escalations} 次</dd>
          </dl>
        ) : <p className="muted">读取中…</p>}
      </Card>
      <Card title="路由决策记录" subtitle="每个任务的报告（顶栏「任务报告」）里有路由与预算：每次决策、升级原因、预算用量。" />
    </div>
  );
}
