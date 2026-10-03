import React,{useEffect,useState} from 'react';

// 始终可见的真实阶段、经过时间与最近一次已完成推理速度。
// Keep real stages, elapsed time and the last completed inference speed visible.
export function LiveStatus({job,working}){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{if(!working)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[working]);
  const labels={planning:'规划',project_planner:'Planner 规划',project_tester:'Tester 测试方案',generation:'生成代码',project_developer:'Developer 生成',verification:'Go 验证',repair:'修复实现',test_revision:'修订测试',project_repair:'修复实现',project_test_revision:'修订测试',planning_retry:'重试测试方案',test_format:'整理测试格式'};
  const rate=job?.last_model_metrics?.generation_tokens_per_second;
  const outcome=job?.run_status==='succeeded'?'验证通过':job?.run_status==='failed'?'验证未通过':job?.status==='completed'?'流程已结束':'等待操作';
  return <div className="live-status" role="status"><strong>{working?'● '+(labels[job?.stage||job?.phase]||'执行中'):'○ '+outcome}</strong><span>{job?.model||'固定模型'}</span>{working&&job?.started&&<span>{Math.max(0,Math.floor(now/1000-job.started))} 秒</span>}<span>{rate!=null?'最近完成调用 '+rate+' tokens/s':working?'等待响应，速率将在本次调用结束后显示':''}</span>{working&&<progress aria-label="当前任务正在运行"/>}</div>;
}
