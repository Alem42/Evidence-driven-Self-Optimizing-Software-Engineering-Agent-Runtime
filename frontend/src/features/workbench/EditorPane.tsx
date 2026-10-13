import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useResults } from '../../api/queries';
import type { Detail } from '../../api/types';
import { FILE_STATE_LABEL, type FileState } from '../../entities/live';
import { diffLines, parseDiagnostics } from '../../entities/text';
import { Button, Segmented } from '../../shared/ui';
import { useDrafts } from '../../stores/ui';
import { ApproveCodeButton } from '../decisions/cards/CodeReviewCard';
import { CodeEditor } from '../code/CodeEditor';
import { useCodeFiles, useSourceReview } from '../code/useCode';

interface Props {
  detail: Detail;
  code: ReturnType<typeof useCodeFiles>;
  path: string;
  content: string;
  state: FileState;
  /** 模型正在修改这个文件时，新片段的长度（字符）。 Length of the new fragment while the model edits this file. */
  fragment?: number;
  following: boolean;
  onFollow: (on: boolean) => void;
}

// 编辑器：代码逐字出现（写入中只读并跟随最新一行）；写完后可切换到“相对上一版本”的 diff；审核阶段可编辑。
// Editor: code appears character by character (read-only and following the last line while written); switch to a diff against the previous version once written; editable during review.
export function EditorPane({ detail, code, path, content, state, fragment, following, onFollow }: Props) {
  const review = useSourceReview(detail);
  const results = useResults(detail.run.data.project_bundle ? detail : undefined);
  const [params] = useSearchParams();
  const [mode, setMode] = useState<'file' | 'diff'>('file');
  const edit = useDrafts((s) => s.edit);
  const line = Number(params.get('line')) || undefined;
  const streaming = state === 'writing';

  useEffect(() => setMode('file'), [path]);

  // 标注来源：源码检查 + 失败的验证输出。 Markers: source review findings plus failing tool output.
  const markers = useMemo(() => {
    const fromReview = (review.report?.findings ?? []).filter((f) => f.path === path && f.line).map((f) => ({ line: f.line!, message: f.message }));
    const failing = (results.data?.checks ?? [])
      .filter((c) => c.result && (c.result.status !== 'completed' || c.result.exit_code !== 0))
      .flatMap((c) => parseDiagnostics((c.result?.stdout ?? '') + '\n' + (c.result?.stderr ?? '')));
    return [...fromReview, ...failing.filter((d) => d.path === path).map((d) => ({ line: d.line!, message: d.message }))];
  }, [review.report, results.data, path]);

  const base = code.base?.[path];
  const canDiff = base !== undefined && base !== content && !streaming;
  const readOnly = !code.editable || code.locked(path) || state === 'writing' || state === 'editing';
  const lines = content ? content.split('\n').length : 0;

  return (
    <div className="editor-pane">
      <div className="editor-bar">
        <code title={path}>{path}</code>
        {state !== 'sealed' && <span className={`fstate-pill fs-${state}`}>{FILE_STATE_LABEL[state]}</span>}
        <span className="muted">{lines} 行{readOnly && !streaming ? ' · 只读' : ''}</span>
        <span className="spacer" />
        {streaming && (
          <Button size="sm" variant={following ? 'default' : 'primary'} onClick={() => onFollow(!following)} title="编辑器是否自动打开并滚动到模型正在写的文件">
            {following ? '● 跟随写入中' : '▶ 跟随写入'}
          </Button>
        )}
        {canDiff && <Segmented value={mode} onChange={setMode} options={[['file', '当前'], ['diff', '相对上一版']]} />}
        {code.dirty && <Button size="sm" variant="ghost" onClick={() => useDrafts.getState().reset(detail.run.id)}>放弃编辑</Button>}
        {code.editable && <ApproveCodeButton detail={detail} />}
      </div>
      {state === 'editing' && <div className="editor-note">模型正在修改这个文件{fragment ? `（新片段已写 ${fragment.toLocaleString()} 字）` : ''}；完成后会显示新的内容。</div>}
      {mode === 'diff' && canDiff ? (
        <pre className="diff">
          {diffLines(base!, content).map((r, i) => (
            <div key={i} className={'diff-' + r.kind}>
              <span>{r.kind === 'add' ? '+' : r.kind === 'del' ? '-' : ' '}</span>
              {r.text}
            </div>
          ))}
        </pre>
      ) : (
        <CodeEditor
          key={path}
          path={path}
          value={content}
          readOnly={readOnly}
          tail={streaming && following}
          markers={markers}
          jumpTo={line}
          onChange={(v) => {
            review.set(null);
            edit(detail.run.id, path, v, code.original ?? {});
          }}
        />
      )}
      {markers.length > 0 && <div className="marker-bar">⚠ 本文件有 {markers.length} 处问题，已在行内标红</div>}
    </div>
  );
}
