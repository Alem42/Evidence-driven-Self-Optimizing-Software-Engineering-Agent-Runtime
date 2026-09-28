import React,{useState} from 'react';
import {projectJob,ProjectProgress} from './ProjectProgress';

// 失败证据由后端绑定；用户反馈只补充修复目标。 Server-bound failure evidence remains authoritative over optional feedback.
export function RepairPanel({detail,profiles,select}) {
  const [feedback,setFeedback]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[job,setJob]=useState(null);
  const [profile,setProfile]=useState('');
  async function repair(){setBusy(true);setError('');try{const r=await projectJob('/runs/'+detail.run.id+'/repair-project',{feedback,api_profile_id:profile||profiles?.active_id},setJob);select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="panel repair-panel"><h3>验证失败 · 下一步修复</h3><p>将当前代码和真实失败输出交给 Developer。原测试和模块配置保持冻结，修复草稿需要你审核。</p>
    <label>补充说明（可选）<textarea rows={2} maxLength={4000} value={feedback} disabled={busy} onChange={e=>setFeedback(e.target.value)} placeholder="例如：保留现有接口，修复编译错误，不更改测试预期。"/></label>
    <label>修复使用的 API<select disabled={busy} value={profile||profiles?.active_id||''} onChange={e=>setProfile(e.target.value)}><option value="">选择已配置密钥的 API</option>{profiles?.profiles?.filter(p=>p.key_configured).map(p=><option key={p.id} value={p.id}>{p.name||p.model}</option>)}</select></label>
    {busy&&<ProjectProgress job={job}/>}<button className="primary" disabled={busy||!profiles?.profiles?.some(p=>p.key_configured)} onClick={repair}>{busy?'正在生成修复草稿…':'根据失败证据生成修复（一次 API 调用）'}</button>
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
