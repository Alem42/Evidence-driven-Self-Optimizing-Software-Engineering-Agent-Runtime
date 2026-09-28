import React,{useEffect,useState} from 'react';
import {api} from '../../api/client';

// 优先展示有语义的结果；原始数据留作折叠证据。 Render semantic role output before optional raw evidence.
export function RoleResult({stage}){
  const [value,setValue]=useState(null),[error,setError]=useState('');
  useEffect(()=>{let stopped=false;setValue(null);setError('');if(stage?.response_ref)api('/runs/'+stage.run_id+'/artifacts/'+stage.response_ref).then(r=>{if(!stopped)setValue(r.artifact);}).catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;};},[stage?.id,stage?.response_ref]);
  if(!stage)return null;
  return <section className="panel role-result"><div className="section-heading"><h3>{stage.label} · 输出</h3><span className="muted">结果与执行状态分开展示</span></div>
    {error&&<p className="error">{error}</p>}
    {!value&&<p className="muted">{stage.status==='running'?'正在等待模型返回完整结果…':stage.response_ref?'正在读取结果…':'此节点没有模型响应；审核或执行结果见下方。'}</p>}
    {value?.summary&&<p>{value.summary}</p>}
    {value?.files&&Array.isArray(value.files)&&<ul className="file-summary">{value.files.map(f=><li key={f.path}><code>{f.path}</code><span>{f.purpose}</span></li>)}</ul>}
    {value?.acceptance&&<ol>{value.acceptance.map((a,i)=><li key={i}>{a}</li>)}</ol>}
    {Array.isArray(value)&&value.map((c,i)=><article key={i}><h4>{c.operation}</h4><p>{c.purpose}</p>{c.cases?.map((t,n)=><details key={n}><summary>{t.level} · {t.name}</summary><pre>输入：{t.input}{'\n'}预期：{t.expected}</pre></details>)}</article>)}
    {value&&<details><summary>原始结构化证据</summary><pre>{JSON.stringify(value,null,2)}</pre></details>}
  </section>;
}
