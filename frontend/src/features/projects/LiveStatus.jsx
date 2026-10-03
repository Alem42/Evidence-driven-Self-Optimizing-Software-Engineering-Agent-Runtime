import React,{useEffect,useState} from 'react';
import {liveStatus} from './workspaceState';

// 始终可见的真实阶段、经过时间与最近一次已完成推理速度。
// Keep real stages, elapsed time and the last completed inference speed visible.
export function LiveStatus({job,working,detail}){
  const [now,setNow]=useState(Date.now());
  const status=liveStatus(job,working,detail);
  useEffect(()=>{setNow(Date.now());if(!status.active)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[status.active]);
  const rate=job?.last_model_metrics?.generation_tokens_per_second;
  const generation=job?.generation_progress;
  // 文件计数来自已持久化结果，不把文件进度误作 token 完成比例。
  // Display persisted file completions without inventing token progress.
  return <div className="live-status" role="status"><strong>{(status.active?'● ':'○ ')+status.title}</strong><span>{job?.model||'固定模型'}</span>{status.active&&job?.started&&<span>{Math.max(0,Math.floor(now/1000-job.started))} 秒</span>}{status.active&&generation&&<span title={generation.current}>文件 {generation.completed} / {generation.total}{generation.current?' · '+generation.current:''}</span>}<span>{rate!=null?'最近完成调用 '+rate+' tokens/s':status.active?'等待响应，速率将在本次调用结束后显示':''}</span>{status.active&&<progress aria-label="当前任务正在运行"/>}</div>;
}
