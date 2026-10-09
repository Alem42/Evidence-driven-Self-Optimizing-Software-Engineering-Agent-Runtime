import type { ReactNode } from 'react';

export interface BarRow { label: ReactNode; value: number | null; note?: ReactNode; tone?: 'ok' | 'bad' | 'accent' | 'muted' }

/** 横向条形图：不引入图表库，用 div 画。value 取 0–1。 Horizontal bars drawn with divs (no chart library); value is 0-1. */
export function Bars({ rows, max = 1, percent = true }: { rows: BarRow[]; max?: number; percent?: boolean }) {
  return (
    <div className="bars">
      {rows.map((r, i) => (
        <div key={i} className="bar-row">
          <span className="bar-label">{r.label}</span>
          <span className="bar-track"><i className={`bar-fill ${r.tone ?? 'accent'}`} style={{ width: `${r.value == null ? 0 : Math.min(100, (100 * r.value) / max)}%` }} /></span>
          <span className="bar-value">{r.value == null ? '—' : percent ? `${Math.round(r.value * 100)}%` : r.value}{r.note ? <small> {r.note}</small> : null}</span>
        </div>
      ))}
    </div>
  );
}
