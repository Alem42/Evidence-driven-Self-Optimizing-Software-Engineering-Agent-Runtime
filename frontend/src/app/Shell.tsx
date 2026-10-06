import { useEffect, type CSSProperties, type PointerEvent as RPointerEvent } from 'react';
import { Outlet } from 'react-router-dom';
import { ActivityProvider } from './activity';
import { Sidebar } from '../features/sidebar/Sidebar';
import { Toasts } from '../shared/Toasts';
import { ReportDialog } from '../features/report/ReportDialog';
import { useUi } from '../stores/ui';
import { useBootstrap } from '../api/queries';

/** 拖动分隔条调整面板宽度。 Drag handle for resizing a pane. */
export function Resizer({ side, min, max }: { side: 'sidebarWidth' | 'inspectorWidth'; min: number; max: number }) {
  const set = useUi((s) => s.set);
  function down(e: RPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const startX = e.clientX;
    const start = useUi.getState()[side];
    const sign = side === 'sidebarWidth' ? 1 : -1;
    const move = (ev: PointerEvent) => set({ [side]: Math.min(max, Math.max(min, start + sign * (ev.clientX - startX))) });
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing');
    };
    document.body.classList.add('resizing');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
  return <div className={`resizer resizer-${side}`} onPointerDown={down} role="separator" aria-orientation="vertical" />;
}

// 演示部署的提示条：告诉访客这里是只读展示，避免“点了没反应”的困惑。
// Banner for demo deployments: tells visitors this is a read-only showcase so a click that does nothing is not a mystery.
function DemoBanner() {
  const demo = useBootstrap().data?.demo;
  if (!demo) return null;
  return <div className="demo-banner" role="status">演示模式（只读）：可以浏览历史任务、报告、评测结果与设置；不能发起或修改任务。 Read-only demo.</div>;
}

export function Shell() {
  const theme = useUi((s) => s.theme);
  const sidebarWidth = useUi((s) => s.sidebarWidth);
  useEffect(() => {
    if (theme === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <ActivityProvider>
      <div className="shell" style={{ '--sw': sidebarWidth + 'px' } as CSSProperties}>
        <Sidebar />
        <Resizer side="sidebarWidth" min={220} max={420} />
        <main className="main">
          <DemoBanner />
          <Outlet />
        </main>
      </div>
      <ReportDialog />
      <Toasts />
    </ActivityProvider>
  );
}
