import React,{useState} from 'react';
import {projectJob,ProjectProgress} from './progress';
import {api} from '../../api/client';

// 失败证据由后端绑定；用户反馈只补充修复目标。 Server-bound failure evidence remains authoritative over optional feedback.
export function RepairPanel({detail,profiles,select,onProgress}) {
  const [feedback,setFeedback]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[job,setJob]=useState(null);
  const [profile,setProfile]=useState('');
  async function repair(){setBusy(true);setError('');try{const r=await projectJob('/runs/'+detail.run.id+'/repair-project',{feedback,api_profile_id:profile||profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function reviseTests(){setBusy(true);setError('');try{const r=await projectJob('/runs/'+detail.run.id+'/revise-project-tests',{feedback,api_profile_id:profile||profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  const [cycle,setCycle]=useState(false);
  React.useEffect(()=>{let stop=false;api('/runs/'+detail.run.id+'/results').then(r=>{if(!stop)setCycle(r.checks.some(c=>JSON.stringify(c.result||{}).includes('import cycle not allowed in test')));}).catch(()=>{});return()=>{stop=true;};},[detail.run.id]);
  return <section className="panel repair-panel"><h3>验证失败 · 下一步修复</h3><p>将当前代码和真实失败输出交给 Developer。原测试和模块配置保持冻结，修复草稿需要你审核。</p>
    {cycle&&<p className="error">检测到测试文件循环导入。普通实现修复无法修改被冻结的测试文件；请创建测试修订，检查断言后重新验证。</p>}
    <label>补充说明（可选）<textarea rows={2} maxLength={4000} value={feedback} disabled={busy} onChange={e=>setFeedback(e.target.value)} placeholder="例如：保留现有接口，修复编译错误，不更改测试预期。"/></label>
    <label>修复使用的 API<select disabled={busy} value={profile||profiles?.active_id||''} onChange={e=>setProfile(e.target.value)}><option value="">选择已配置密钥的 API</option>{profiles?.profiles?.filter(p=>p.key_configured).map(p=><option key={p.id} value={p.id}>{p.name||p.model}</option>)}</select></label>
    {busy&&<ProjectProgress job={job}/>}<div className="action-bar"><button className="primary" disabled={busy||cycle||!profiles?.profiles?.some(p=>p.key_configured)} onClick={repair}>{busy?'正在生成草稿…':'修复实现（冻结测试）'}</button><button disabled={busy||!profiles?.profiles?.some(p=>p.key_configured)} onClick={reviseTests}>修订测试（新版本）</button></div>
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
