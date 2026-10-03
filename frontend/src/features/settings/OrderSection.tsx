import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk, useProfiles } from '../../api/queries';
import type { Profile } from '../../api/types';
import { byRouting, normalizeProfiles, profileLevel, profileReady } from '../../entities/profiles';
import { moveRow, normalizeLevels, type OrderRow } from '../../entities/order';
import { useUi } from '../../stores/ui';
import { Badge, Button, Card, Notice } from '../../shared/ui';

type Row = OrderRow;

// 模型与次序：拖动、上下箭头或直接输入位置来调整尝试顺序；勾选决定本任务用哪些。
// Model order: drag, use the arrows, or type a position; ticks decide which models a ladder task uses.
export function OrderSection() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const profiles = useProfiles();
  const selected = useUi((s) => s.selectedModels);
  const set = useUi((s) => s.set);
  const list = useMemo(() => normalizeProfiles(profiles.data).profiles.slice().sort(byRouting), [profiles.data]);
  const byId = useMemo(() => new Map(list.map((p) => [p.id, p])), [list]);
  const [rows, setRows] = useState<Row[]>([]);
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);

  useEffect(() => setRows(list.map((p) => ({ id: p.id, level: profileLevel(p) }))), [list]);

  const original = useMemo(() => list.map((p) => p.id + ':' + profileLevel(p)).join('|'), [list]);
  const dirty = rows.map((r) => r.id + ':' + r.level).join('|') !== original;
  const readyIds = useMemo(() => list.filter(profileReady).map((p) => p.id), [list]);
  const chosen = useMemo(() => new Set(selected ?? readyIds), [selected, readyIds]);

  const save = useMutation({
    mutationFn: () => api('/settings/order', { order: rows.map((r) => r.id), levels: Object.fromEntries(rows.map((r) => [r.id, r.level])) }),
    onSuccess: (result) => qc.setQueryData(qk.profiles, normalizeProfiles(result as never)),
  });

  const toggle = (id: string, on: boolean) => {
    const base = new Set(selected ?? readyIds);
    if (on) base.add(id);
    else base.delete(id);
    set({ selectedModels: [...base] });
  };
  const move = (from: number, to: number) => setRows((r) => moveRow(r, from, to));

  // 预览：按等级分组，同级之间轮换。 Preview grouped by level; siblings take turns.
  const ladder = useMemo(() => {
    const groups = new Map<number, string[]>();
    rows.filter((r) => chosen.has(r.id) && profileReady(byId.get(r.id) as Profile)).forEach((r) => {
      const p = byId.get(r.id) as Profile;
      groups.set(r.level, [...(groups.get(r.level) ?? []), p.name || p.model]);
    });
    return [...groups.entries()].sort((a, b) => a[0] - b[0]);
  }, [rows, chosen, byId]);

  if (profiles.isLoading) return <p className="muted">读取配置…</p>;
  if (!list.length) return <Notice tone="warn">还没有模型。先到「模型配置」或「API 账户」添加。</Notice>;

  return (
    <div className="stack">
      {params.get('from') === 'new' && (
        <Notice tone="ok">
          这里调整的是「本地优先 · 有界升级」任务的尝试顺序和参与的模型。
          <button className="link" onClick={() => navigate('/')}>返回新建任务</button>
        </Notice>
      )}
      <Card
        title="尝试次序"
        subtitle="从上到下依次尝试：靠前的先用，失败后才轮到后面的；同一等级的模型会轮流使用。拖动行、点箭头，或直接修改“位置”。"
        actions={
          <>
            {dirty && <Badge tone="warn">未保存</Badge>}
            <Button size="sm" title="把每个模型设为独立的一级：严格按从上到下的顺序逐个尝试，不再同级轮换" onClick={() => setRows((cur) => cur.map((r, i) => ({ ...r, level: i + 1 })))}>严格按顺序</Button>
            <Button size="sm" disabled={!dirty} onClick={() => setRows(list.map((p) => ({ id: p.id, level: profileLevel(p) })))}>还原</Button>
            <Button variant="primary" size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>{save.isPending ? '保存中…' : '保存次序'}</Button>
          </>
        }
      >
        {save.isError && <Notice tone="bad">{(save.error as Error).message}</Notice>}
        <div className="order-list" role="list">
          {rows.map((r, i) => {
            const p = byId.get(r.id);
            if (!p) return null;
            const ready = profileReady(p);
            return (
              <div
                key={r.id}
                role="listitem"
                className={`order-row ${drag === i ? 'dragging' : ''} ${over === i && drag !== null && drag !== i ? 'over' : ''} ${ready ? '' : 'unready'}`}
                draggable
                onDragStart={(e) => { setDrag(i); e.dataTransfer.effectAllowed = 'move'; }}
                onDragOver={(e) => { e.preventDefault(); setOver(i); }}
                onDragEnd={() => { setDrag(null); setOver(null); }}
                onDrop={(e) => { e.preventDefault(); if (drag !== null) move(drag, i); setDrag(null); setOver(null); }}
              >
                <span className="grip" aria-hidden="true" title="拖动调整">⋮⋮</span>
                <input
                  className="pos"
                  type="number"
                  min={1}
                  max={rows.length}
                  aria-label={`${p.name || p.model} 的位置`}
                  value={i + 1}
                  onChange={(e) => { const to = Number(e.target.value) - 1; if (Number.isInteger(to)) move(i, Math.min(rows.length - 1, Math.max(0, to))); }}
                />
                <label className="use" title="本任务是否使用这个模型">
                  <input type="checkbox" checked={chosen.has(r.id)} disabled={!ready} onChange={(e) => toggle(r.id, e.target.checked)} />
                </label>
                <div className="who">
                  <strong>{p.name || p.model}</strong>
                  <small className="mono muted">{p.model}</small>
                </div>
                <Badge tone={p.model_type === 'local' ? 'ok' : 'running'}>{p.model_type === 'local' ? '本地' : 'API'}</Badge>
                <label className="level" title="等级：同级轮换，更高等级才算升级">
                  L
                  <input type="number" min={1} max={100} aria-label="等级" value={r.level} onChange={(e) => setRows((cur) => normalizeLevels(cur.map((x) => (x.id === r.id ? { ...x, level: Number(e.target.value) || 1 } : x))))} />
                </label>
                <span className="muted ctx">{p.context_limit ? Math.round(p.context_limit / 1024) + 'K' : '—'}</span>
                <span className="muted price">{p.input_price_per_million != null ? `${p.input_price_per_million}/${p.output_price_per_million ?? '—'}` : '无价格'}</span>
                <span className="row">
                  <button className="icon-btn" aria-label="上移" disabled={i === 0} onClick={() => move(i, i - 1)}>▲</button>
                  <button className="icon-btn" aria-label="下移" disabled={i === rows.length - 1} onClick={() => move(i, i + 1)}>▼</button>
                </span>
                {!ready && <Badge tone="neutral">{p.enabled === false ? '已停用' : '缺少密钥'}</Badge>}
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="本任务的尝试链预览" subtitle="只包含已勾选且可用的模型。每个阶段从最低等级开始；同级失败后轮换，用尽次数才升到下一等级。">
        {ladder.length ? (
          <div className="ladder">
            {ladder.map(([level, names], n) => (
              <span key={level} className="row">
                {n > 0 && <span className="arrow">→</span>}
                <span className="tier"><b>L{level}</b> {names.join(' ⇄ ')}</span>
              </span>
            ))}
          </div>
        ) : <p className="muted">没有勾选任何可用模型。</p>}
        <p className="hint">等级沿列表只增不减；把高等级模型拖到前面时，它会自动跟随上方邻居的等级。「严格按顺序」会让每个模型独立成一级（此时「最多升级次数」要够用，在「路由与预算」里调）。勾选只影响「本地优先 · 有界升级」任务，固定模型任务按新建页上选的那个模型执行。</p>
      </Card>
    </div>
  );
}
