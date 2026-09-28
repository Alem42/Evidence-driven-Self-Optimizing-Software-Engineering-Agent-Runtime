import {useCallback,useEffect,useState} from 'react';
import {api} from '../api/client';

// 统一项目选择、任务进度与读取轮询，组件不自行管理全局运行状态。
// Centralize project selection and polling instead of duplicating global state in views.
export function useWorkbench(){
  const [projects,setProjects]=useState([]),[selected,setSelected]=useState(()=>localStorage.getItem('masa.selected')||'');
  const [detail,setDetail]=useState(null),[view,setView]=useState(null),[profiles,setProfiles]=useState(null);
  const [jobId,setJobId]=useState(()=>localStorage.getItem('masa.job')||''),[job,setJob]=useState(null);
  const [error,setError]=useState(''),[online,setOnline]=useState(false),[boot,setBoot]=useState(null);
  const select=useCallback(id=>{setSelected(id);setDetail(null);setView(null);localStorage.setItem('masa.selected',id);},[]);
  const track=useCallback(j=>{if(j?.job_id&&j.status==='running'){setJobId(j.job_id);localStorage.setItem('masa.job',j.job_id);}},[]);
  useEffect(()=>{Promise.all([api('/bootstrap'),api('/settings')]).then(([b,p])=>{setBoot(b);setProfiles(p);}).catch(e=>setError(e.message));},[]);
  useEffect(()=>{
    let stopped=false,timer;
    async function poll(){
      try{
        const catalog=await api('/projects');
        let target=selected;
        if(jobId){
          let j;
          try{j={...await api('/jobs/'+jobId),job_id:jobId};}catch(e){if(!stopped){setJobId('');localStorage.removeItem('masa.job');setError(e.message);}return;}
          if(stopped)return;
          setJob(j);
          if(j.run_id){target=j.run_id;if(target!==selected){setSelected(target);localStorage.setItem('masa.selected',target);}}
          if(j.status!=='running'){setJobId('');localStorage.removeItem('masa.job');if(j.error)setError(j.error);}
        }
        const [d,v]=target?await Promise.all([api('/runs/'+target),api('/projects/'+target)]):[null,null];
        if(stopped)return;
        setProjects(catalog.projects);setDetail(d);setView(v);setOnline(true);
      }catch(e){if(!stopped){setError(e.message);setOnline(false);}}
      finally{if(!stopped)timer=setTimeout(poll,900);}
    }
    poll();return()=>{stopped=true;clearTimeout(timer);};
  },[selected,jobId]);
  async function start(goal,profile){
    setError('');const result=await api('/projects/plan',{goal,api_profile_id:profile,background:true});
    setJob({status:'running',started:Date.now()/1000});setJobId(result.job_id);localStorage.setItem('masa.job',result.job_id);
  }
  return {projects,selected,select,detail,view,profiles,setProfiles,job,track,start,error,setError,online,boot,
    working:Boolean(jobId)||Boolean(detail?.active)||['planning','generating'].includes(detail?.run.data.project_plan?.status)};
}
