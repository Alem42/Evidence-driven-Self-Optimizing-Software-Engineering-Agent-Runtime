import { useRoles } from '../../api/queries';
import { levelText, roleGroups, writesText, type RoleInfo } from '../../entities/roles';
import { Badge, Card } from '../../shared/ui';

// 角色：清单来自后端 RoleSpec 注册表（/api/roles），只读展示。
// Roles: the list comes from the backend RoleSpec registry (/api/roles); read-only.
function RoleRows({ roles }: { roles: RoleInfo[] }) {
  return (
    <table className="table">
      <thead><tr><th>角色</th><th>用途</th><th>等级</th><th>权限</th><th>出场方式</th></tr></thead>
      <tbody>
        {roles.map((r) => (
          <tr key={r.id}>
            <td><strong>{r.label}</strong><div className="mono muted">{r.id}</div></td>
            <td>{r.description}</td>
            <td>{levelText(r.level)}</td>
            <td><Badge tone={r.permissions.writes === 'none' ? 'ok' : 'warn'}>{writesText(r.permissions.writes)}</Badge></td>
            <td>{r.routable ? '流水线固定步骤' : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RolesSection() {
  const { data: roles } = useRoles();
  const groups = roleGroups(roles ?? []);
  return (
    <div className="stack">
      <Card title="角色" subtitle="角色就是数据（后端 roles/specs/*.json 与 *.prompt.md）；顺序、默认等级、权限都来自 RoleSpec。">
        <RoleRows roles={groups.core} />
      </Card>
    </div>
  );
}
