import React,{useEffect,useState} from 'react';
import {api} from './api';

// 文件内容来自不可变草稿，批准时提交当前完整集合。 Load immutable draft files and submit the complete visible bundle.
export function ProjectCodeReview({detail,select}) {
  const plan=detail.run.data.project_plan;
  const [files,setFiles]=useState(null),[path,setPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{let stopped=false;setFiles(null);setError('');if(!plan.files_ref)return;
    const ref=plan.approval_ref||plan.files_ref;
    api('/runs/'+detail.run.id+'/artifacts/'+ref).then(r=>{if(!stopped){const value=plan.approval_ref?r.artifact.files:r.artifact;setFiles(value);setPath(Object.keys(value)[0]);}}).catch(e=>{if(!stopped)setError(e.message);});
    return()=>{stopped=true;};
  },[detail.run.id,plan.files_ref,plan.approval_ref]);
  async function approve(){setBusy(true);setError('');try{const r=await api('/runs/'+detail.run.id+'/approve-project-code',{files_ref:plan.files_ref,files});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  const pending=plan.status==='awaiting_review'&&detail.run.status==='paused'&&!detail.run.cancel_requested;
  return <section className="panel code-review"><h3>Developer · 多文件代码审核</h3><p>逐文件查看实现与测试。批准后创建独立项目文件夹，运行 test、vet 和格式检查。模型编写的测试不等于独立业务验收。</p>
    {(error||plan.error)&&<p className="error">{error||plan.error}</p>}{files&&<><label>项目文件<select value={path} onChange={e=>setPath(e.target.value)}>{Object.keys(files).map(n=><option key={n}>{n}</option>)}</select></label>
    <label>{path}<textarea className="code-editor" rows={22} readOnly={!pending||busy} value={files[path]||''} onChange={e=>setFiles({...files,[path]:e.target.value})}/></label>
    <button className="primary" disabled={busy||detail.run.cancel_requested||!['awaiting_review','approved'].includes(plan.status)} onClick={approve}>{busy?'准备项目与验证…':plan.status==='approved'?'查看或继续已批准项目':'批准整套代码并验证'}</button></>}
    <p className="hint">最多 20 个文件；未批准前只保存草稿，不执行代码。文件路径来自已确认架构。</p>
  </section>;
}
