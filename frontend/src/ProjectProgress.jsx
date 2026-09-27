import React from 'react';
import {api} from './api';

// 轮询服务端阶段，显示已用时间而不是虚构完成比例。 Poll actual server stages, not invented completion percentages.
export async function projectJob(path,body,onProgress) {
  const {job_id}=await api(path,{...body,background:true});
  while(true){
    const job=await api('/jobs/'+job_id);onProgress(job);
    if(job.status==='completed')return job.result;
    if(job.status==='failed')throw new Error(job.error+(job.run_id?' · 运行记录 '+job.run_id:''));
    await new Promise(resolve=>setTimeout(resolve,800));
  }
}

export function ProjectProgress({job}) {
  const names={project_planner:'Planner 正在设计项目结构',project_tester:'Tester 正在制定验证方案',project_developer:'Developer 正在生成代码和测试'};
  return <div className="notice" role="status"><strong>{names[job?.stage]||'正在准备请求'}</strong><progress aria-label="等待模型响应"/><span>{job?.started?Math.max(0,Math.floor(Date.now()/1000-job.started)):0} 秒 · 等待模型返回完整响应</span></div>;
}

// 以真实对象阶段展示主流程；不把未来检查节点显示为正在执行。 Display real lifecycle phases without pretending planned checks are executing.
export function ProjectFlow({detail}) {
  const p=detail.run.data.project_plan;
  const stage=p?(p.kind==='code'?2:0):3;
  const labels=['方案与验收','确认方案','代码与测试','审核并验证','查看结果'];
  const current=p?(p.status==='approved'?stage+1:stage):(detail.active?3:4);
  return <section className="panel"><div className="tabs">{labels.map((label,i)=><span key={label} className={'badge '+(i===current?'running':i<current?'succeeded':'pending')}>{i+1}. {label}</span>)}</div><p className="hint">模型提出方案和代码；人工批准后 Runtime 执行检查，Gate 独立判定。已完成项目可一键重新验证。</p></section>;
}
