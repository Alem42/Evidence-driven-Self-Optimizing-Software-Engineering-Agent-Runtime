// CodeMirror 6 封装：Go 高亮、只读/可编辑、行级问题标注、跳转到指定行。
// CodeMirror 6 wrapper: Go highlighting, read-only/editable, line markers and jump-to-line.
import { useEffect, useRef } from 'react';
import { basicSetup } from 'codemirror';
import { EditorState, StateEffect, StateField, type Extension } from '@codemirror/state';
import { Decoration, EditorView, type DecorationSet } from '@codemirror/view';
import { go } from '@codemirror/lang-go';
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
}

export function CodeEditor({ value, readOnly, onChange, markers = [], jumpTo }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    const extensions: Extension[] = [
      basicSetup,
      go(),
      syntaxHighlighting(highlight),
      theme,
      markerField,
      EditorState.readOnly.of(readOnly),
      EditorView.editable.of(!readOnly),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) onChangeRef.current?.(u.state.doc.toString());
      }),
    ];
    view.current = new EditorView({ parent: host.current!, state: EditorState.create({ doc: value, extensions }) });
    return () => view.current?.destroy();
    // 只读状态变化时重建；内容同步见下方 effect。 Rebuild only when read-only changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly]);

  // 外部内容变化（切换文件）才整体替换，用户输入不回写，光标不跳动。
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

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
