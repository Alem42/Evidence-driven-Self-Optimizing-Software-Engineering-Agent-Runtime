import React,{useEffect,useState} from 'react';
import {liveStatus} from './workspaceState';

// 始终可见的真实阶段、经过时间与最近一次已完成推理速度。
// Keep real stages, elapsed time and the last completed inference speed visible.
export function LiveStatus({job,working,detail}){
  const [now,setNow]=useState(Date.now());
  const status=liveStatus(job,working,detail);
  useEffect(()=>{setNow(Date.now());if(!status.active)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[status.active]);
  const rate=job?.last_model_metrics?.generation_tokens_per_second;
  return <div className="live-status" role="status"><strong>{(status.active?'● ':'○ ')+status.title}</strong><span>{job?.model||'固定模型'}</span>{status.active&&job?.started&&<span>{Math.max(0,Math.floor(now/1000-job.started))} 秒</span>}<span>{rate!=null?'最近完成调用 '+rate+' tokens/s':status.active?'等待响应，速率将在本次调用结束后显示':''}</span>{status.active&&<progress aria-label="当前任务正在运行"/>}</div>;
}
