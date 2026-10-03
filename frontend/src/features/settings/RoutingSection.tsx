import { useProfiles } from '../../api/queries';
import { byRouting, profileLabel, profileLevel, profileReady } from '../../entities/profiles';
import { Badge, Card, Notice } from '../../shared/ui';

// 路由与预算：目前只展示“按等级/优先级排序的候选”（真实配置）。自动升级、预算、辅助路由
// 后端尚未实现，这里只放占位说明，实现后在此接入编辑能力。
// Routing & budget: shows the real candidate order; escalation/budget/router are not implemented yet.
export function RoutingSection() {
  const { data } = useProfiles();
  const list = [...(data?.profiles ?? [])].sort(byRouting);
  return (
    <div className="stack">
      <Notice tone="warn">自动升级、全任务预算和辅助路由尚未在后端实现。当前任务使用固定模型，不会自动切换。下面是你配置的候选顺序预览。</Notice>
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
      <Card title="预算（预留）" subtitle="跨规划/生成/修复累计的调用次数、时间、云 token 与费用上限。" />
      <Card title="路由决策记录（预留）" subtitle="任务页 Inspector 将显示每次实际选用的模型、升级原因与用量。" />
    </div>
  );
}
