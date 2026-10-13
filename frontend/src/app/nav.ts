import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useUi } from '../stores/ui';

/** 打开某个版本。manual=true 表示用户主动浏览，会关闭“跟随执行”，后台事件不再抢页面。
 *  tab 是旧版“标签页”的说法：工作台只用它决定要不要顺手打开底部面板（checks → 输出，history → 版本）。 */
export function useOpenRun() {
  const navigate = useNavigate();
  const { search } = useLocation();
  return useCallback(
    (runId: string, opts: { manual?: boolean; tab?: string; params?: Record<string, string> } = {}) => {
      if (opts.manual) useUi.getState().set({ follow: false });
      const q = new URLSearchParams(opts.tab || opts.params ? '' : search);
      if (opts.tab) q.set('tab', opts.tab);
      Object.entries(opts.params ?? {}).forEach(([k, v]) => q.set(k, v));
      const s = q.toString();
      navigate('/task/' + runId + (s ? '?' + s : ''));
    },
    [navigate, search],
  );
}
