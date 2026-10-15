import { useState } from 'react';
import { api } from '../../api/client';
import type { PackageInstall, TriagePackages } from '../../api/types';
import { Button, Notice } from '../../shared/ui';

/**
 * 需求里点名了第三方包（例如 numpy）：按设置里的策略处理。
 * ask：先问你，批准才装进项目环境；allow（完全访问）：开始任务时自动装；deny：只用标准库。装的地方永远是项目环境（.tools/pyenv），不碰系统环境。
 * The goal names third-party packages (e.g. numpy): handled by the policy in Settings. ask: you approve first, then they go into the project environment; allow (full access): installed automatically when the task starts;
 * deny: standard library only. The place is always the project environment (.tools/pyenv); the system environment is never touched.
 */
export function PackagesNotice({ packages, language, onInstalled, onStdlibOnly }: { packages: TriagePackages; language: string; onInstalled: () => void; onStdlibOnly: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!packages.missing.length) return null;
  const names = packages.missing.join('、');
  const approve = async () => {
    setBusy(true);
    setError('');
    try {
      await api<PackageInstall>(`/languages/${language}/packages`, { packages: packages.missing, approve: true });
      onInstalled();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Notice tone="warn">
      <div className="stack">
        <span>
          这个需求点名了第三方包：<strong>{names}</strong>，项目环境里还没有。
          {packages.policy === 'ask' && `装进项目环境（${packages.env_exists ? `已占用 ${packages.size_mb} MB，` : ''}上限 ${packages.max_mb} MB），不会碰系统环境。要装吗？`}
          {packages.policy === 'allow' && `当前是“完全访问”：开始任务时会自动装进项目环境（上限 ${packages.max_mb} MB）。`}
          {packages.policy === 'deny' && '设置里第三方包是“禁止”：只能用 Python 标准库。'}
        </span>
        <div className="row wrap">
          {packages.policy === 'ask' && <Button variant="primary" size="sm" disabled={busy} onClick={approve}>{busy ? '安装中…（可能要几十秒）' : `允许安装 ${names}`}</Button>}
          <Button size="sm" disabled={busy} onClick={onStdlibOnly}>改成只用标准库</Button>
          {error && <span className="error">{error}</span>}
        </div>
      </div>
    </Notice>
  );
}
