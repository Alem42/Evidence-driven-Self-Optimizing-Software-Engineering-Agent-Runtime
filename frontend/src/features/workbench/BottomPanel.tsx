import { lazy, Suspense } from 'react';
import type { Detail, LiveCall, ProjectView } from '../../api/types';
import { Resizer } from '../../shared/Resizer';
import { PlanCard } from '../decisions/cards/PlanCard';
import { Spinner } from '../../shared/ui';
import { useUi } from '../../stores/ui';
import { OutputPanel } from './OutputPanel';
import { DeliveryPanel } from './DeliveryPanel';
import { ReportPanel } from './ReportPanel';
import { VersionsPanel } from './VersionsPanel';

// 追踪面板点开才加载。 The trace panel is loaded on demand.
const TracePanel = lazy(() => import('./TracePanel').then((m) => ({ default: m.TracePanel })));

type Tab = 'output' | 'plan' | 'trace' | 'versions' | 'report' | 'delivery';

// 底部面板：默认折叠成一行标签；点哪个标签就只加载哪个。
// Bottom panel: collapsed to one row of tabs by default; only the tab you open is loaded.
export function BottomPanel({ detail, view, live, hasPlanSpec }: { detail: Detail; view?: ProjectView; live: LiveCall | null; hasPlanSpec: boolean }) {
  const open = useUi((s) => s.bottomOpen);
  const tab = useUi((s) => s.bottomTab) as Tab;
  const height = useUi((s) => s.bottomHeight);
  const set = useUi((s) => s.set);
  const failed = detail.run.status === 'failed' && Boolean(detail.run.data.project_bundle);
  const tabs: [Tab, string][] = [['output', '输出'], ...(hasPlanSpec ? ([['plan', '方案']] as [Tab, string][]) : []), ['trace', '追踪 · 检视'], ['versions', '版本'], ['report', '报告'], ['delivery', '交付']];
  const active: Tab = tabs.some(([t]) => t === tab) ? tab : 'output';
  const choose = (t: Tab) => set(open && t === active ? { bottomOpen: false } : { bottomOpen: true, bottomTab: t });

  return (
    <section className={`bottom ${open ? 'open' : ''}`} style={open ? { height } : undefined} aria-label="底部面板">
      {open && <Resizer target="bottomHeight" axis="y" min={160} max={640} invert />}
      <div className="bottom-tabs" role="tablist">
        {tabs.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={open && t === active} className={open && t === active ? 'on' : ''} onClick={() => choose(t)}>
            {label}
            {t === 'output' && failed && <i className="attn bad" />}
          </button>
        ))}
        <span className="spacer" />
        <button className="icon-btn" onClick={() => set({ bottomOpen: !open })} title={open ? '折叠' : '展开'} aria-label={open ? '折叠面板' : '展开面板'}>{open ? '▾' : '▴'}</button>
      </div>
      {open && (
        <div className="bottom-body">
          <Suspense fallback={<div className="center pad"><Spinner /> 加载…</div>}>
            {active === 'output' && <OutputPanel detail={detail} live={live} />}
            {active === 'plan' && <div className="pad"><PlanCard key={detail.run.id} detail={detail} /></div>}
            {active === 'trace' && <TracePanel detail={detail} view={view} live={Boolean(detail.active || detail.role_active)} />}
            {active === 'versions' && <VersionsPanel detail={detail} view={view} />}
            {active === 'report' && <ReportPanel runId={detail.run.id} live={Boolean(detail.active || detail.role_active)} />}
            {active === 'delivery' && <DeliveryPanel runId={detail.run.id} live={Boolean(detail.active || detail.role_active)} />}
          </Suspense>
        </div>
      )}
    </section>
  );
}
