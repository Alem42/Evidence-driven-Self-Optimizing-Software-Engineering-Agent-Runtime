import { NavLink, Navigate, useParams } from 'react-router-dom';
import { DiagnosticsSection } from './DiagnosticsSection';
import { HarnessSection } from './HarnessSection';
import { LocalSection } from './LocalSection';
import { ModelsSection } from './ModelsSection';
import { RoutingSection } from './RoutingSection';

const SECTIONS = [
  ['models', '模型配置', '本地与云端模型、等级、角色权限'],
  ['local', '本地运行时', 'Ollama 模型、加载与硬件'],
  ['routing', '路由与预算', '本地优先 / 升级策略（规划中）'],
  ['harness', 'Harness', 'Go 工具链、runner、检查策略'],
  ['diagnostics', '诊断', '后台异常记录'],
] as const;

// 设置中心：独立页面而非弹窗，每个二级入口一个分区；新能力在此新增分区即可。
// Settings hub: one section per secondary entry; add a section here for new backend features.
export function SettingsPage() {
  const { section } = useParams();
  if (!SECTIONS.some(([id]) => id === section)) return <Navigate to="/settings/models" replace />;
  return (
    <div className="settings">
      <nav className="settings-nav" aria-label="设置分区">
        <h2>设置</h2>
        {SECTIONS.map(([id, label, desc]) => (
          <NavLink key={id} to={'/settings/' + id} className={({ isActive }) => (isActive ? 'on' : '')}>
            <strong>{label}</strong>
            <small>{desc}</small>
          </NavLink>
        ))}
      </nav>
      <div className="settings-body">
        {section === 'models' && <ModelsSection />}
        {section === 'local' && <LocalSection />}
        {section === 'routing' && <RoutingSection />}
        {section === 'harness' && <HarnessSection />}
        {section === 'diagnostics' && <DiagnosticsSection />}
      </div>
    </div>
  );
}
