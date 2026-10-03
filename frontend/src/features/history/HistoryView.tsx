import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Detail, ProjectView } from '../../api/types';
import { useOpenRun } from '../../app/nav';
import { statusText } from '../../entities/status';
import { Badge, Button, Card, Spinner } from '../../shared/ui';

const KIND = { plan: '方案', code: '代码草稿', verification: '工具验证' } as const;
const tone = (s: string) => (s === 'succeeded' ? 'ok' : s === 'failed' ? 'bad' : s === 'paused' ? 'wait' : 'neutral');

// 日志与版本：按需读取日志（只在打开此标签时），版本列表是任务的分支历史。
// Logs & versions: logs load only on demand; versions are the task's branch history.
export function HistoryView({ detail, view }: { detail: Detail; view?: ProjectView }) {
  const open = useOpenRun();
  const [wantLog, setWantLog] = useState(false);
  const [copied, setCopied] = useState(false);
  const log = useQuery({ queryKey: ['logs', detail.run.id], queryFn: async () => (await api<{ text: string }>('/runs/' + detail.run.id + '/logs')).text, enabled: wantLog, refetchInterval: wantLog && detail.active ? 2000 : false });

  return (
    <div className="history-view">
      <Card title="版本记录" subtitle="修复与测试修订都会生成新版本，旧版本不会被覆盖。">
        <div className="versions">
          {[...(view?.versions ?? [])].reverse().map((v) => (
            <button key={v.run_id} className={v.run_id === detail.run.id ? 'current' : ''} onClick={() => open(v.run_id, { manual: true })}>
              <span>{KIND[v.kind]}</span>
              <code>{v.run_id.slice(0, 8)}</code>
              <Badge tone={tone(v.status)}>{statusText[v.status] ?? v.status}</Badge>
            </button>
          ))}
        </div>
      </Card>
      <Card
        title="来源链日志"
        actions={
          <>
            <Button size="sm" onClick={() => setWantLog(true)} disabled={log.isFetching}>{log.data === undefined ? '读取日志' : '刷新'}</Button>
            {log.data !== undefined && (
              <Button size="sm" variant="ghost" onClick={async () => { await navigator.clipboard.writeText(log.data!); setCopied(true); }}>{copied ? '已复制' : '复制'}</Button>
            )}
          </>
        }
      >
        {log.isFetching && log.data === undefined && <Spinner />}
        {log.error && <p className="bad-text">{(log.error as Error).message}</p>}
        {log.data !== undefined && <pre className="log">{log.data || '暂无日志'}</pre>}
      </Card>
      <Card title="技术信息">
        <p className="path muted">Run：{detail.run.id} · {detail.event_count} 个事件</p>
      </Card>
    </div>
  );
}
