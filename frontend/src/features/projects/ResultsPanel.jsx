import React, {useEffect, useState} from 'react';
import {api} from '../../api/client';

// 将 Go JSON 测试输出转换为可读日志，保留其他工具原文。 Render Go test output while preserving other tool logs.
export function readableOutput(value='') {
  return value.split('\n').map(line=>{try {const item=JSON.parse(line); return typeof item.Output==='string'?item.Output.trimEnd():item.Action&&item.Package?'':line;}catch{return line;}}).filter(Boolean).join('\n');
}

// 仅在账本结果引用变化时重新取日志，避免每秒加载完整输出。 Fetch logs only when ledger references change.
export function ResultsPanel({detail}) {
  const [checks,setChecks]=useState([]), [error,setError]=useState('');
  const revision=detail.tools.map(t=>t.id+':'+t.result_ref).join('|');
  useEffect(()=>{let stopped=false;setChecks([]);setError('');api('/runs/'+detail.run.id+'/results').then(r=>{if(!stopped)setChecks(r.checks);}).catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;};},[detail.run.id,revision]);
  const names={go_test:'测试与编译',go_vet:'静态检查',go_fmt_check:'代码格式',go_index:'代码索引'};
  const finished=checks.filter(c=>c.result),failed=finished.filter(c=>c.result.status!=='completed'||c.result.exit_code!==0);
  return <section className="panel results-panel"><div className="panel-head"><h3>运行结果</h3><strong className={failed.length?'result-fail':'result-pass'}>{failed.length?`${failed.length} 项未通过`:detail.active?'正在验证':detail.run.status==='succeeded'?'Gate 已通过':'等待结果'}</strong></div><p>{finished.length} / {checks.length} 项工具已返回结果。{failed.length?'失败输出已展开，可在下方发起修复。':'检查详情可展开查看。'}</p><p className="hint">Harness 在隔离副本执行 Go 工具。没有验收测试时，检查通过不代表业务需求已经满足。当前不启动应用服务。</p>
    <p className="path">代码文件夹：{detail.run.data.workspace}</p>
    {!checks.length&&<p>尚未执行工具。生成的草稿需要先在「代码与审核」中批准。</p>}
    {error&&<p className="error">{error}</p>}
    {checks.map(c=>{const r=c.result, ok=r?.status==='completed'&&r.exit_code===0;return <article className="check-result" key={c.id}>
      <div className="panel-head"><h3>{names[c.operation] || c.operation}</h3><strong className={ok?'result-pass':'result-fail'}>{!r?'执行中':ok?'通过':'未通过'}{r&&` · ${r.duration_ms ?? 0} ms`}</strong></div>
      {r&&<><p>退出码：{r.exit_code ?? '无'} · {r.status}{r.truncated?' · 输出已截断':''}</p>{r.error&&<p className="error">{r.error}</p>}
        <details open={!ok}><summary>查看输出</summary><pre>{readableOutput(r.stdout)||'（无标准输出）'}</pre>{r.stderr&&<pre>{r.stderr}</pre>}</details></>}
    </article>;})}
  </section>;
}
