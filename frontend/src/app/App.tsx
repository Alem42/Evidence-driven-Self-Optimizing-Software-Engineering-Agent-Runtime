import { Navigate, Route, Routes } from 'react-router-dom';
import { Shell } from './Shell';
import { NewTask } from '../features/newtask/NewTask';
import { Workbench } from '../features/workbench/Workbench';
import { SettingsPage } from '../features/settings/SettingsPage';

// 路由即状态：/task/:runId?file=main.go&node=… 刷新、前进后退都能还原。
// URL is state: task, file and node survive refresh and back/forward.
export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<NewTask />} />
        <Route path="task/:runId" element={<Workbench />} />
        <Route path="settings" element={<Navigate to="/settings/models" replace />} />
        <Route path="settings/:section" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
