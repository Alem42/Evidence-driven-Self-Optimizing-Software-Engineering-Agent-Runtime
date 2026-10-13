import type { PointerEvent as RPointerEvent } from 'react';
import { useUi } from '../stores/ui';

type SizeKey = 'sidebarWidth' | 'treeWidth' | 'bottomHeight';

/** 拖动分隔条调整面板尺寸：axis=x 调宽度，axis=y 调高度；invert 表示向左/向上拖动是变大。
 *  Drag handle for a pane size: x changes a width, y a height; invert means dragging left/up grows it. */
export function Resizer({ target, axis, min, max, invert = false }: { target: SizeKey; axis: 'x' | 'y'; min: number; max: number; invert?: boolean }) {
  const set = useUi((s) => s.set);
  function down(e: RPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const origin = axis === 'x' ? e.clientX : e.clientY;
    const start = useUi.getState()[target];
    const sign = invert ? -1 : 1;
    const move = (ev: PointerEvent) => set({ [target]: Math.min(max, Math.max(min, start + sign * ((axis === 'x' ? ev.clientX : ev.clientY) - origin))) });
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing', 'resizing-y');
    };
    document.body.classList.add(axis === 'x' ? 'resizing' : 'resizing-y');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
  return <div className={`resizer resizer-${axis}`} onPointerDown={down} role="separator" aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'} />;
}
