import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Detail, ProjectView } from '../../api/types';
import { useOpenRun } from '../../app/nav';
import { statusText } from '../../entities/status';
import { useUi } from '../../stores/ui';
import { Badge, Button, Spinner } from '../../shared/ui';

const KIND = { plan: '方案', code: '代码草稿', verification: '工具验证' } as const;
const tone = (s: string) => (s === 'succeeded' ? 'ok' : s === 'failed' ? 'bad' : s === 'paused' ? 'wait' : 'neutral');

// 版本：修复与测试修订都会生成新版本，旧版本不会被覆盖；点一个版本就切过去。来源链日志只在点开时才读取。
// Versions: every repair or test revision makes a new version and the old one is never overwritten; click one to switch. The provenance log is read only on demand.
export function VersionsPanel({ detail, view }: { detail: Detail; view?: ProjectView }) {
  const open = useOpenRun();
  const follow = useUi((s) => s.follow);
  const set = useUi((s) => s.set);
  const [wantLog, setWantLog] = useState(false);
  const log = useQuery({
    queryKey: ['logs', detail.run.id],
    queryFn: async () => (await api<{ text: string }>('/runs/' + detail.run.id + '/logs')).text,
    enabled: wantLog,
    refetchInterval: wantLog && detail.active ? 2000 : false,
  });

  return (
    <div className="versions-panel">
      <section>
        <div className="row between">
          <h4>版本记录</h4>
          <label className="switch" title="开启后自动跟随正在执行的版本；手动选择会关闭">
            <input type="checkbox" checked={follow} onChange={(e) => set({ follow: e.target.checked })} />
            <span>跟随执行</span>
          </label>
        </div>
        <div className="versions">
          {[...(view?.versions ?? [])].reverse().map((v) => (
            <button key={v.run_id} className={v.run_id === detail.run.id ? 'current' : ''} onClick={() => open(v.run_id, { manual: true })}>
              <span>{KIND[v.kind]}</span>
              <code>{v.run_id.slice(0, 8)}</code>
              <Badge tone={tone(v.status)}>{statusText[v.status] ?? v.status}</Badge>
            </button>
          ))}
        </div>
        <p className="path muted">Run：{detail.run.id} · {detail.event_count} 个事件</p>
      </section>
      <section>
        <div className="row between">
          <h4>来源链日志</h4>
          <span className="row">
            <Button size="sm" onClick={() => setWantLog(true)} disabled={log.isFetching}>{log.data === undefined ? '读取日志' : '刷新'}</Button>
            {log.data !== undefined && <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(log.data!)}>复制</Button>}
          </span>
        </div>
        {log.isFetching && log.data === undefined && <Spinner />}
        {log.error && <p className="bad-text">{(log.error as Error).message}</p>}
        {log.data !== undefined && <pre className="log">{log.data || '暂无日志'}</pre>}
      </section>
    </div>
  );
}
