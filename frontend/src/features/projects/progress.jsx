import React from 'react';
import {api} from '../../api/client';

// 轮询服务端阶段，显示已用时间而不是虚构完成比例。 Poll actual server stages, not invented completion percentages.
export async function projectJob(path,body,onProgress) {
  const {job_id}=await api(path,{...body,background:true});
  while(true){
    const job={...await api('/jobs/'+job_id),job_id};onProgress(job);
    if(job.status==='completed')return job.result;
    if(job.status==='failed')throw new Error(job.error+(job.run_id?' · 运行记录 '+job.run_id:''));
    await new Promise(resolve=>setTimeout(resolve,800));
  }
}

export function ProjectProgress({job}) {
  const names={project_planner:'Planner 正在设计项目结构',project_tester:'Tester 正在制定验证方案',project_developer:'Developer 正在生成代码和测试',project_repair:'Developer 正在根据失败证据修复实现',project_test_revision:'Tester 正在修订测试文件',verification:'正在运行 Go 检查',generation:'正在生成代码',planning_retry:'Tester 方案未通过契约，正在重试'};
  return <div className="notice" role="status"><strong>{names[job?.stage]||'正在准备请求'}</strong><progress aria-label="等待模型响应"/><span>{job?.started?Math.max(0,Math.floor(Date.now()/1000-job.started)):0} 秒 · 等待模型返回完整响应</span></div>;
}

