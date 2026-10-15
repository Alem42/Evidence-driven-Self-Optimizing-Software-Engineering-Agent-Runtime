// CodeMirror 6 封装：Go 高亮、只读/可编辑、行级问题标注、跳转到指定行。
// CodeMirror 6 wrapper: Go highlighting, read-only/editable, line markers and jump-to-line.
import { useEffect, useRef } from 'react';
import { basicSetup } from 'codemirror';
import { Annotation, EditorState, StateEffect, StateField, type Extension } from '@codemirror/state';
import { Decoration, EditorView, type DecorationSet } from '@codemirror/view';
import { go } from '@codemirror/lang-go';
import { python } from '@codemirror/lang-python';
import { highlightOf } from '../../entities/languages';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.controlKeyword], color: 'var(--syn-keyword)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--syn-string)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--syn-number)' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: 'var(--syn-comment)', fontStyle: 'italic' },
  { tag: [t.typeName, t.className, t.namespace], color: 'var(--syn-type)' },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: 'var(--syn-func)' },
]);

const theme = EditorView.theme({
  '&': { height: '100%', backgroundColor: 'var(--code-bg)', color: 'var(--text)', fontSize: '13px' },
  '.cm-scroller': { fontFamily: 'var(--mono)', lineHeight: '1.6' },
  '.cm-gutters': { backgroundColor: 'var(--code-bg)', color: 'var(--faint)', border: 'none' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--hover)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'var(--accent-soft) !important' },
  '.cm-line-problem': { backgroundColor: 'var(--bad-soft)', boxShadow: 'inset 2px 0 0 var(--bad)' },
});

export interface Marker {
  line: number;
  message: string;
}

const setMarkers = StateEffect.define<Marker[]>();
/** 标记“不是用户改的”：程序替换内容（切换文件、流式写入）不能当成用户编辑回写。 Marks a change that did not come from the user (switching files, streaming) so it is never saved as an edit. */
const external = Annotation.define<boolean>();
const markerField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, tr) {
    for (const e of tr.effects) {
      if (e.is(setMarkers)) {
        const doc = tr.state.doc;
        return Decoration.set(
          e.value
            .filter((m) => m.line >= 1 && m.line <= doc.lines)
            .sort((a, b) => a.line - b.line)
            .map((m) => Decoration.line({ class: 'cm-line-problem', attributes: { title: m.message } }).range(doc.line(m.line).from)),
        );
      }
    }
    return value.map(tr.changes);
  },
  provide: (f) => EditorView.decorations.from(f),
});

interface Props {
  value: string;
  readOnly: boolean;
  onChange?: (value: string) => void;
  markers?: Marker[];
  jumpTo?: number;
  /** 文件路径：按后缀选语法高亮。 The file path: picks the syntax highlighting by extension. */
  path?: string;
  /** 流式写入中：新内容只是在末尾追加（不整体替换），并保持滚动到最新一行。 Streaming: new content is appended at the end (no whole replacement) and the view follows the last line. */
  tail?: boolean;
}

export function CodeEditor({ value, readOnly, onChange, markers = [], jumpTo, path = '', tail = false }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    const extensions: Extension[] = [
      basicSetup,
      ...(highlightOf(path) === 'python' ? [python()] : highlightOf(path) === 'go' ? [go()] : []),
      syntaxHighlighting(highlight),
      theme,
      markerField,
      EditorState.readOnly.of(readOnly),
      EditorView.editable.of(!readOnly),
      EditorView.updateListener.of((u) => {
        if (u.docChanged && !u.transactions.some((tr) => tr.annotation(external))) onChangeRef.current?.(u.state.doc.toString());
      }),
    ];
    view.current = new EditorView({ parent: host.current!, state: EditorState.create({ doc: value, extensions }) });
    return () => view.current?.destroy();
    // 只读状态变化时重建；内容同步见下方 effect。 Rebuild only when read-only changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly, path]);

  // 外部内容变化（切换文件）才整体替换，用户输入不回写，光标不跳动；流式写入时新内容以旧内容开头，只追加差额。
  // Whole replacement only when the content changes from outside (switching files); while streaming the new text starts with the old, so only the difference is appended.
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const current = v.state.doc.toString();
    if (current === value) return;
    const append = value.startsWith(current);
    v.dispatch({
      changes: append ? { from: current.length, insert: value.slice(current.length) } : { from: 0, to: current.length, insert: value },
      annotations: external.of(true),
      ...(tail ? { effects: EditorView.scrollIntoView(value.length, { y: 'end' }) } : {}),
    });
  }, [value, tail]);

  useEffect(() => {
    view.current?.dispatch({ effects: setMarkers.of(markers) });
  }, [markers, value]);

  useEffect(() => {
    const v = view.current;
    if (!v || !jumpTo || jumpTo < 1 || jumpTo > v.state.doc.lines) return;
    const line = v.state.doc.line(jumpTo);
    v.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: 'center' }) });
  }, [jumpTo, value]);

  return <div className="code-editor" ref={host} />;
}
