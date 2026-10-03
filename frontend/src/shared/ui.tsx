// 轻量 UI 基元：统一外观，业务组件只组合它们。 Small UI primitives shared by every feature.
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { Tone } from '../entities/status';

type ButtonVariant = 'default' | 'primary' | 'ghost' | 'danger';

export function Button({ variant = 'default', size, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: 'sm' }) {
  return <button type="button" {...props} className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : ''} ${className}`} />;
}

export function Badge({ tone = 'neutral', children, dot }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`badge tone-${tone}`}>
      {dot && <i className={`dot ${tone === 'running' ? 'pulse' : ''}`} />}
      {children}
    </span>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <i className="spinner" style={{ width: size, height: size }} role="status" aria-label="处理中" />;
}

export function Card({ title, subtitle, tone, actions, children, className = '' }: { title?: ReactNode; subtitle?: ReactNode; tone?: Tone; actions?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <section className={`card ${tone ? 'card-' + tone : ''} ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div className="card-title">
            {title && <h3>{title}</h3>}
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Notice({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="segmented" role="tablist">
      {options.map(([v, label]) => (
        <button key={v} role="tab" aria-selected={v === value} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  );
}
