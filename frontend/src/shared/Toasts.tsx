import { useNavigate } from 'react-router-dom';
import { useToasts } from '../stores/ui';

export function Toasts() {
  const { toasts, dismiss } = useToasts();
  const navigate = useNavigate();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <span>{t.text}</span>
          {t.to && (
            <button
              className="link"
              onClick={() => {
                navigate(t.to!);
                dismiss(t.id);
              }}
            >
              {t.action ?? '查看'}
            </button>
          )}
          <button className="icon-btn" aria-label="关闭" onClick={() => dismiss(t.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
