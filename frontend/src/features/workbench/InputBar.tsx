import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { sendInput } from '../../api/queries';
import type { Detail } from '../../api/types';
import { useToasts } from '../../stores/ui';
import { Button } from '../../shared/ui';

// 底部输入框：人在回路。追加一条提示（下一个节点开始时生效，不打断正在进行的调用）；暂停 / 继续 / 停止。
// The input bar: human in the loop. Add a note (applies when the next node starts, never interrupting a call in flight); pause / resume / stop.
export function InputBar({ detail, live, paused, disabled }: { detail: Detail; live: boolean; paused: boolean; disabled?: boolean }) {
  const [text, setText] = useState('');
  const qc = useQueryClient();
  const push = useToasts((s) => s.push);
  const runId = detail.run.id;
  const notes = detail.events.filter((e) => e.type === 'user_input' && e.payload.kind === 'note').map((e) => String(e.payload.text));

  const note = useMutation({
    mutationFn: (value: string) => sendInput(runId, { kind: 'note', text: value }),
    onSuccess: () => {
      setText('');
      qc.invalidateQueries({ queryKey: ['detail', runId] });
      push({ tone: 'ok', text: live ? '提示已记录，下一个节点开始时生效。' : '提示已记录，下一次调用会带上它。' });
    },
    onError: (e: Error) => push({ tone: 'bad', text: e.message }),
  });
  const control = useMutation({
    mutationFn: (action: 'pause' | 'resume' | 'stop') => sendInput(runId, { kind: 'control', action }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['job'] });
      qc.invalidateQueries({ queryKey: ['detail'] });
    },
    onError: (e: Error) => push({ tone: 'bad', text: e.message }),
  });

  const send = () => {
    const value = text.trim();
    if (value && !note.isPending) note.mutate(value);
  };

  return (
    <footer className="wb-input">
      {notes.length > 0 && (
        <div className="wb-notes" title="这个版本里你追加过的提示">
          {notes.slice(-3).map((n, i) => (
            <span key={i} className="note-chip">✎ {n.length > 48 ? n.slice(0, 48) + '…' : n}</span>
          ))}
          {notes.length > 3 && <span className="muted">另有 {notes.length - 3} 条</span>}
        </div>
      )}
      <div className="wb-input-row">
        <textarea
          rows={1}
          maxLength={600}
          value={text}
          disabled={disabled}
          aria-label="追加提示"
          placeholder={live ? '追加提示：下一个节点开始时生效，例如“金额保留两位小数”（Enter 发送，Shift+Enter 换行）' : '追加提示：下一次修复或继续时会带上它（Enter 发送）'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        <Button size="sm" variant="primary" disabled={disabled || !text.trim() || note.isPending} onClick={send}>发送</Button>
        {live && (
          <>
            {paused ? (
              <Button size="sm" disabled={control.isPending} onClick={() => control.mutate('resume')}>▶ 继续</Button>
            ) : (
              <Button size="sm" disabled={control.isPending} onClick={() => control.mutate('pause')} title="在下一个节点边界停住；正在进行的模型调用会先结束">⏸ 暂停</Button>
            )}
            <Button size="sm" variant="danger" disabled={control.isPending} onClick={() => control.mutate('stop')} title="停止这个任务，保留已有代码与证据">■ 停止</Button>
          </>
        )}
      </div>
    </footer>
  );
}
