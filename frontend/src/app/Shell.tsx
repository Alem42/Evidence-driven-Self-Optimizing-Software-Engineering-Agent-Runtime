import { useEffect, type CSSProperties } from 'react';
import { Outlet, useMatch } from 'react-router-dom';
import { ActivityProvider } from './activity';
import { Sidebar } from '../features/sidebar/Sidebar';
import { Toasts } from '../shared/Toasts';
import { Resizer } from '../shared/Resizer';
import { ReportDialog } from '../features/report/ReportDialog';
import { useUi } from '../stores/ui';
import { useBootstrap } from '../api/queries';

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
  const sidebarOpen = useUi((s) => s.sidebarOpen);
  // 任务页是工作台：任务列表默认收起，让代码占满；新建页与设置页始终显示它。
  // A task page is the workbench: the task list starts closed so the code gets the room; the new-task and settings pages always show it.
  const onTask = Boolean(useMatch('/task/:runId'));
  const showSidebar = !onTask || sidebarOpen;
  useEffect(() => {
    if (theme === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <ActivityProvider>
      <div className={`shell ${showSidebar ? '' : 'no-sidebar'}`} style={{ '--sw': sidebarWidth + 'px' } as CSSProperties}>
        {showSidebar && (
          <>
            <Sidebar />
            <Resizer target="sidebarWidth" axis="x" min={200} max={420} />
          </>
        )}
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
