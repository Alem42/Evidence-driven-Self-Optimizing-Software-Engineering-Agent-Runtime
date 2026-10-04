import { useEffect } from 'react';
import { NavLink, Navigate, useNavigate, useParams } from 'react-router-dom';
import { Button } from '../../shared/ui';
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
  const navigate = useNavigate();
  // 返回上一页（没有历史就回主页）；Esc 同样关闭设置。 Back to the previous page (home when there is no history); Esc closes too.
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate('/'));
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (e.key === 'Escape' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') back();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  if (!SECTIONS.some(([id]) => id === section)) return <Navigate to="/settings/models" replace />;
  return (
    <div className="settings">
      <nav className="settings-nav" aria-label="设置分区">
        <div className="nav-actions">
          <Button size="sm" onClick={back} title="返回上一页 (Esc)">← 返回</Button>
          <Button size="sm" variant="ghost" onClick={() => navigate('/')} title="回到主页面">⌂ 主页</Button>
        </div>
        <h2>设置</h2>
        {SECTIONS.map(([id, label, desc]) => (
          <NavLink key={id} to={'/settings/' + id} className={({ isActive }) => (isActive ? 'on' : '')}>
            <strong>{label}</strong>
            <small>{desc}</small>
          </NavLink>
        ))}
      </nav>
      <div className="settings-body">
        <div className="settings-close">
          <Button size="sm" variant="ghost" onClick={back} aria-label="关闭设置">✕ 关闭设置</Button>
        </div>
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
