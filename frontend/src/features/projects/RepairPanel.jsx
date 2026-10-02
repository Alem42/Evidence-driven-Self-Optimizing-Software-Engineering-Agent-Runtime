import {profileReady,profileLabel} from '../settings/profiles';
import React,{useState} from 'react';
import {projectJob,ProjectProgress} from './progress';
import {api} from '../../api/client';

// 失败证据由后端绑定；用户反馈只补充修复目标。 Server-bound failure evidence remains authoritative over optional feedback.
export function RepairPanel({detail,profiles,select,onProgress}) {
  const [feedback,setFeedback]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[job,setJob]=useState(null);
  const [profile,setProfile]=useState('');
  async function repair(){setBusy(true);setError('');try{const r=await projectJob('/runs/'+detail.run.id+'/repair-project',{feedback,api_profile_id:profile||profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function reviseTests(){setBusy(true);setError('');try{const r=await projectJob('/runs/'+detail.run.id+'/revise-project-tests',{feedback,api_profile_id:profile||profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  const [advice,setAdvice]=useState(null);
  // 使用后端统一分类，避免手动和自动模式对同一日志给出不同建议。
  // Use server diagnosis so manual and automatic repair agree on the same evidence.
  React.useEffect(()=>{let stop=false;setAdvice(null);api('/runs/'+detail.run.id+'/results').then(r=>{
    if(!stop)setAdvice(r.repair_advice||{action:'repair',message:'请查看日志判断应修复实现还是测试。'});
  }).catch(e=>{if(!stop)setError(e.message);});return()=>{stop=true;};},[detail.run.id]);
  const formatOnly=advice?.action==='format_tests',cycle=formatOnly||advice?.action==='revise_tests';
  async function formatTests(){setBusy(true);setError('');try{select((await api('/runs/'+detail.run.id+'/format-project-tests',{})).id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="panel repair-panel"><h3>验证失败 · 下一步修复</h3><p>选择修改实现，或在测试本身无法编译时创建单独的测试修订；每次修订都形成新版本并重新验证。</p>
    {advice&&<p className={cycle?'error':'notice'}>{advice.message}</p>}
    <label>补充说明（可选）<textarea rows={2} maxLength={4000} value={feedback} disabled={busy} onChange={e=>setFeedback(e.target.value)} placeholder="例如：保留现有接口，修复编译错误，不更改测试预期。"/></label>
    <label>修复使用的模型<select disabled={busy} value={profile||profiles?.active_id||''} onChange={e=>setProfile(e.target.value)}><option value="">选择可用模型</option>{profiles?.profiles?.filter(profileReady).map(p=><option key={p.id} value={p.id}>{profileLabel(p)}</option>)}</select></label>
    {busy&&<ProjectProgress job={job}/>}<div className="action-bar">{formatOnly&&<button className="primary" disabled={busy} onClick={formatTests}>格式化测试文件（无模型调用）</button>}<button className={!cycle?'primary':''} disabled={busy||!advice||cycle||!profiles?.profiles?.some(profileReady)} onClick={repair}>{busy?'正在生成草稿…':'修复实现（冻结测试）'}</button><button className={cycle&&!formatOnly?'primary':''} disabled={busy||!profiles?.profiles?.some(profileReady)} onClick={reviseTests}>修订测试（新版本）</button></div>
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
