import React, {useState, useEffect} from 'react';
import {api} from './api';

// 人工控制生成与批准，模型响应不会自动写入。 Human controls generation and approval; model output never writes automatically.
export function CodeGeneration({profiles, active, select, close, initial={}}) {
  const [form,setForm]=useState({goal:'',repo:'',target:'solution.go',tests:'',api_profile_id:profiles?.active_id || '',...initial});
  const [busy,setBusy]=useState(false), [error,setError]=useState('');
  // 一次请求对应一次付费生成；重复点击被禁用。 One request is one billed generation; duplicate clicks are disabled.
  async function generate(e){e.preventDefault();setBusy(true);setError('');try{const result=await api('/generate',form);select(result.id);close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  const ready=profiles?.profiles?.filter(p=>p.key_configured) || [];
  return <div className="overlay"><section className="modal inspector" role="dialog" aria-modal="true" aria-label="生成 Go 代码">
    <div className="panel-head"><h2>用需求生成 Go 代码</h2><button disabled={busy} onClick={close}>关闭</button></div>
    <p className="notice">生成后先由你预览和编辑，点击批准才写入隔离副本并运行 test、vet、格式检查。当前一次生成一个 Go 实现文件。</p>
    <form onSubmit={generate}>
      <label>API 配置<select required value={form.api_profile_id} onChange={e=>setForm({...form,api_profile_id:e.target.value})}><option value="">选择已配置密钥的 API</option>{ready.map(p=><option key={p.id} value={p.id}>{p.name || p.model}</option>)}</select></label>
      <label>描述要实现的功能<textarea required maxLength={16000} rows={6} value={form.goal} onChange={e=>setForm({...form,goal:e.target.value})} placeholder="例如：实现 Clamp(value, min, max int) (int, error)，限制数值范围；min 大于 max 时返回错误。"/></label>
      <label>已有 Go 仓库路径（留空创建新项目）<input value={form.repo} onChange={e=>setForm({...form,repo:e.target.value})}/></label>
      {form.repo&&<label>目标文件（仓库相对路径，须已存在）<input required value={form.target} onChange={e=>setForm({...form,target:e.target.value})}/></label>}
      {!form.repo&&<label>验收测试（可选；package solution）<textarea rows={4} value={form.tests} onChange={e=>setForm({...form,tests:e.target.value})} placeholder="可粘贴 solution_test.go；留空时验证编译、vet 和格式，不证明业务需求正确。"/></label>}
      {error&&<p className="error" role="alert">{error}</p>}
      {!ready.length&&<p className="error">请先在 API 管理中保存密钥。服务重启后需重新输入。</p>}
      <button className="primary wide" disabled={busy||!!active||!ready.length}>{busy?'模型正在生成草稿，请稍候…':'生成草稿（调用真实 LLM）'}</button>
    </form></section></div>;
}

// 绑定提案引用，防止过期页面批准了另一份代码。 Bind the proposal reference so a stale page cannot approve another draft.
export function CodeReview({detail,onRevision}) {
  const generation=detail.run.data.codegen;
  const [draft,setDraft]=useState(null), [content,setContent]=useState(''), [error,setError]=useState(''), [busy,setBusy]=useState(false);
  const [feedback,setFeedback]=useState('');
  useEffect(()=>{let stopped=false;setDraft(null);setError('');Promise.all([
    api('/runs/'+detail.run.id+'/artifacts/'+generation.proposal_ref),
    generation.approval_ref?api('/runs/'+detail.run.id+'/artifacts/'+generation.approval_ref):Promise.resolve(null)
  ]).then(([r,approved])=>{if(!stopped){setDraft(r.artifact);setContent(approved?approved.artifact.files[0].content:r.artifact.content);}}).catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;};},[detail.run.id,generation.proposal_ref,generation.approval_ref]);
  // 只提交用户当前看到的完整文件；拒绝不产生写入。 Submit exactly the visible code; rejection performs no write.
  async function review(action){setBusy(true);setError('');try{await api('/runs/'+detail.run.id+'/review-code',{action,proposal_ref:generation.proposal_ref,content});}catch(e){setError(e.message);}finally{setBusy(false);}}
  // 下载当前可见代码，不在服务端执行额外写入。 Download visible code without extra server-side writes.
  function download(){const url=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=generation.target.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  const pending=generation.status==='awaiting_review'&&detail.run.status==='paused'&&!detail.run.cancel_requested;
  return <section className="panel code-review"><h3>Human in the Loop · {generation.status}</h3>
    <p>{draft?.summary}</p><p className="hint">模型生成只是提案。批准后只写入 {generation.target} 的工作副本，源仓库不变。</p><p className="path">代码副本：{detail.run.data.workspace}</p>
    {draft&&<><details><summary>查看修改前代码</summary><pre>{draft.before_content}</pre></details>
      <label>生成代码（审核时可直接修改）<textarea className="code-editor" rows={18} readOnly={!pending} value={content} onChange={e=>setContent(e.target.value)}/></label>
      <button onClick={download}>下载当前代码</button>
      {pending&&<div className="actions"><button className="primary" disabled={busy||!content.trim()||detail.active} onClick={()=>review('approve')}>批准并写入，然后自动验证</button><button disabled={busy||detail.active} onClick={()=>review('reject')}>拒绝此提案</button></div>}
      <label>修改需求，让模型重新生成<textarea rows={3} value={feedback} onChange={e=>setFeedback(e.target.value)}/></label><button disabled={busy||detail.active||!feedback.trim()} onClick={()=>onRevision({goal:detail.run.data.goal+'\n\n追加要求：'+feedback,repo:generation.status==='approved'?detail.run.data.workspace:detail.run.data.source,target:generation.target,parent_run_id:detail.run.id})}>带反馈创建新草稿</button>
    </>}{error&&<p className="error" role="alert">{error}</p>}</section>;
}
