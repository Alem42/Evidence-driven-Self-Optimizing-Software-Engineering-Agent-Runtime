import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Detail } from '../../api/types';
import { diffLines, parseDiagnostics } from '../../entities/text';
import { useDrafts } from '../../stores/ui';
import { Button, Empty, Segmented, Spinner } from '../../shared/ui';
import { useResults } from '../../api/queries';
import { CodeEditor } from './CodeEditor';
import { useCodeFiles, useSourceReview } from './useCode';
import { ApproveCodeButton } from '../thread/cards/CodeReviewCard';

const isTest = (p: string) => p.endsWith('_test.go');

// 代码标签：文件树 + 编辑器；审核阶段可编辑，其余只读；可与修复前内容对比，问题行标红。
// Code tab: file tree + editor; editable only during review; diff against the pre-repair base.
export function CodeView({ detail }: { detail: Detail }) {
  const code = useCodeFiles(detail);
  const review = useSourceReview(detail);
  const results = useResults(detail.run.data.project_bundle ? detail : undefined);
  const [params, setParams] = useSearchParams();
  const [mode, setMode] = useState<'file' | 'diff'>('file');
  const edit = useDrafts((s) => s.edit);

  const paths = useMemo(() => Object.keys(code.files ?? {}).sort((a, b) => Number(isTest(a)) - Number(isTest(b)) || a.localeCompare(b)), [code.files]);
  const wanted = params.get('file');
  const path = wanted && code.files && Object.hasOwn(code.files, wanted) ? wanted : paths.find((p) => p === 'cmd/app/main.go') ?? paths.find((p) => p.endsWith('.go') && !isTest(p)) ?? paths[0];
  const line = Number(params.get('line')) || undefined;

  // 标注来源：源码检查 + 失败的验证输出。 Markers: source review findings plus failing tool output.
  const markers = useMemo(() => {
    const fromReview = (review.report?.findings ?? []).filter((f) => f.path === path && f.line).map((f) => ({ line: f.line!, message: f.message }));
    const failing = (results.data?.checks ?? [])
      .filter((c) => c.result && (c.result.status !== 'completed' || c.result.exit_code !== 0))
      .flatMap((c) => parseDiagnostics((c.result?.stdout ?? '') + '\n' + (c.result?.stderr ?? '')));
    return [...fromReview, ...failing.filter((d) => d.path === path).map((d) => ({ line: d.line!, message: d.message }))];
  }, [review.report, results.data, path]);

  if (!code.hasRef)
    return (
      <Empty title="还没有生成代码">先完成方案，再生成项目。文件会在模型完整响应通过校验后出现，不会展示未经校验的部分 JSON。</Empty>
    );
  if (code.loading || !code.files) return <div className="center pad"><Spinner /> 正在读取文件…</div>;

  const baseText = code.base?.[path];
  const readOnly = !code.editable || code.locked(path);
  const select = (p: string) => setParams((q) => { const n = new URLSearchParams(q); n.set('file', p); n.delete('line'); return n; }, { replace: true });

  return (
    <div className="code-view">
      <nav className="file-tree" aria-label="项目文件">
        <div className="tree-head">{code.isDraft ? (code.editable ? '代码草稿 · 可编辑' : '代码草稿') : '已批准快照'}</div>
        {paths.map((p) => (
          <button key={p} className={`${p === path ? 'current' : ''} ${isTest(p) ? 'is-test' : ''}`} onClick={() => select(p)} title={p}>
            <span className="file-ico">{isTest(p) ? '◇' : '◻'}</span>
            <span className="file-name">{p}</span>
            {code.changed.has(p) && <i className="tag tag-warn">改动</i>}
            {code.dirty && code.original?.[p] !== code.files![p] && <i className="tag tag-accent">已编辑</i>}
            {code.isDraft && code.locked(p) && <i className="tag">冻结</i>}
          </button>
        ))}
      </nav>
      <div className="editor-pane">
        <div className="editor-bar">
          <code>{path}</code>
          <span className="muted">{(code.files[path] ?? '').split('\n').length} 行{readOnly ? ' · 只读' : ''}</span>
          <span className="spacer" />
          {baseText !== undefined && <Segmented value={mode} onChange={setMode} options={[['file', '当前'], ['diff', '对比修复前']]} />}
          {code.dirty && (
            <Button size="sm" variant="ghost" onClick={() => useDrafts.getState().reset(detail.run.id)}>
              放弃编辑
            </Button>
          )}
          {code.editable && <ApproveCodeButton detail={detail} />}
        </div>
        {mode === 'diff' && baseText !== undefined ? (
          <pre className="diff">
            {diffLines(baseText, code.files[path] ?? '').map((r, i) => (
              <div key={i} className={'diff-' + r.kind}>
                <span>{r.kind === 'add' ? '+' : r.kind === 'del' ? '-' : ' '}</span>
                {r.text}
              </div>
            ))}
          </pre>
        ) : (
          <CodeEditor
            key={path}
            value={code.files[path] ?? ''}
            readOnly={readOnly}
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
    </div>
  );
}
