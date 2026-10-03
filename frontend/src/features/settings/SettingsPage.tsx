import { NavLink, Navigate, useParams } from 'react-router-dom';
import { DiagnosticsSection } from './DiagnosticsSection';
import { AccountsSection } from './AccountsSection';
import { HarnessSection } from './HarnessSection';
import { LocalSection } from './LocalSection';
import { ModelsSection } from './ModelsSection';
import { OrderSection } from './OrderSection';
import { RoutingSection } from './RoutingSection';

const SECTIONS = [
  ['models', '模型配置', '本地与云端模型、等级、角色权限'],
  ['order', '模型与次序', '拖动调整尝试顺序；选择本任务用哪些'],
  ['accounts', 'API 账户', '一把 key 的可用模型、余额，一键添加'],
  ['local', '本地运行时', 'Ollama 模型、加载与硬件'],
  ['routing', '路由与预算', '默认预算与策略（可编辑）'],
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
        {section === 'order' && <OrderSection />}
        {section === 'accounts' && <AccountsSection />}
        {section === 'local' && <LocalSection />}
        {section === 'routing' && <RoutingSection />}
        {section === 'harness' && <HarnessSection />}
        {section === 'diagnostics' && <DiagnosticsSection />}
      </div>
    </div>
  );
}
