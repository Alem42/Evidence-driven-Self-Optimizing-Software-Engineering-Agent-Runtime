import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Button, Card, Notice } from '../../shared/ui';

// 后台诊断：按需读取，不自动刷新；记录不含需求正文与密钥。
// Backend diagnostics: on demand only; records contain no request bodies or secrets.
export function DiagnosticsSection() {
  const [wanted, setWanted] = useState(false);
  const [copied, setCopied] = useState(false);
  const q = useQuery({ queryKey: ['diagnostics'], queryFn: () => api<{ errors: unknown[] }>('/diagnostics'), enabled: wanted });
  return (
    <Card
      title="后台诊断 · 异常记录"
      subtitle="记录后台请求的异常类型与代码位置；项目业务失败证据在任务日志中。"
      actions={
        <>
          <Button size="sm" disabled={q.isFetching} onClick={() => { setWanted(true); if (wanted) q.refetch(); }}>{q.isFetching ? '读取中…' : wanted ? '刷新' : '读取诊断'}</Button>
          {q.data && <Button size="sm" variant="ghost" onClick={async () => { await navigator.clipboard.writeText(JSON.stringify(q.data, null, 2)); setCopied(true); }}>{copied ? '已复制' : '复制 JSON'}</Button>}
        </>
      }
    >
      {q.isError && <Notice tone="bad">{(q.error as Error).message}</Notice>}
      {q.data && (
        <>
          <p>{q.data.errors.length ? `最近 ${q.data.errors.length} 条后台异常` : '当前没有记录到后台请求异常。'}</p>
          <pre className="raw">{JSON.stringify(q.data, null, 2)}</pre>
        </>
      )}
    </Card>
  );
}
