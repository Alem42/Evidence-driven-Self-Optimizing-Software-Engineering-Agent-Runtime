import { Link } from 'react-router-dom';
import { useBootstrap, useRoles } from '../../api/queries';
import { levelText, roleGroups, writesText, type RoleInfo } from '../../entities/roles';
import { Badge, Card } from '../../shared/ui';

// 动态角色：角色清单来自后端 RoleSpec 注册表（/api/roles），指挥者状态来自路由默认策略。只读展示；开关在“路由与预算”。
// Dynamic roles: the list comes from the backend RoleSpec registry, the conductor state from the routing defaults. Read-only; the switch lives in Routing & budget.
const WHEN_TEXT: Record<string, string> = {
  only_assertions: '只剩断言失败时',
  repaired_before_pass: '通过 Gate 且经过修复之后',
};

function RoleRows({ roles }: { roles: RoleInfo[] }) {
  return (
    <table className="table">
      <thead><tr><th>角色</th><th>用途</th><th>等级</th><th>权限</th><th>何时出场</th></tr></thead>
      <tbody>
        {roles.map((r) => (
          <tr key={r.id}>
            <td><strong>{r.label}</strong><div className="mono muted">{r.id}</div></td>
            <td>{r.description}</td>
            <td>{levelText(r.level)}</td>
            <td><Badge tone={r.permissions.writes === 'none' ? 'ok' : 'warn'}>{writesText(r.permissions.writes)}</Badge></td>
            <td>{r.when ? WHEN_TEXT[r.when.guard] ?? r.when.guard : r.routable ? '流水线固定步骤' : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RolesSection() {
  const { data: roles } = useRoles();
  const { data: boot } = useBootstrap();
  const policy = boot?.routing_defaults?.policy;
  const on = !!policy?.conductor;
  const groups = roleGroups(roles ?? []);
  return (
    <div className="stack">
      <Card title="指挥者" subtitle="验证失败后，规则无法明确下一步时，才让最强模型在已注册的候选步骤里选一个。" actions={<Badge tone={on ? 'ok' : 'neutral'} dot>{on ? '已开启' : '默认关闭'}</Badge>}>
        <dl className="facts">
          <dt>每个任务最多询问</dt><dd>{policy?.conductor_max_calls ?? 6} 次</dd>
          <dt>级联</dt><dd>{policy?.conductor_cascade ? '先低等级，提议不合法再升级' : '直接用最高等级'}</dd>
          <dt>不合法时</dt><dd>拒绝提议、留下记录，回到确定性规则（任务照常继续）</dd>
          <dt>本地模型</dt><dd>{boot?.local_models ? (boot.local_models.allowed ? '可用' : '已禁用：' + boot.local_models.reason) : '—'}</dd>
        </dl>
        <p className="hint">在 <Link to="/settings/routing">路由与预算</Link> 里开关。开启后，任务报告的“修复过程”会显示每次决策、被拒绝的提议、测试怀疑者的核对结论和代码审阅意见。是否真的有收益要看评测对照，没有证据前保持关闭。</p>
      </Card>
      <Card title="流水线角色" subtitle="每个任务都会用到；顺序与默认等级来自 RoleSpec。">
        <RoleRows roles={groups.core} />
      </Card>
      <Card title="动态角色" subtitle="只读，只在指挥者开启并选中它们时才会运行；它们都不能改文件，也不影响 Gate 的裁决。">
        <RoleRows roles={groups.dynamic} />
      </Card>
      {groups.offline.length > 0 && (
        <Card title="离线角色" subtitle="不参与任务，只在命令行调优（scripts/tune.py）时提议路由策略的改动。">
          <RoleRows roles={groups.offline} />
        </Card>
      )}
    </div>
  );
}
