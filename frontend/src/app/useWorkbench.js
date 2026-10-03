import {useCallback,useEffect,useState} from 'react';
import {api} from '../api/client';
import {pollTarget} from '../features/projects/workspaceState';

// 统一项目选择、任务进度与读取轮询，组件不自行管理全局运行状态。
// Centralize project selection and polling instead of duplicating global state in views.
export function useWorkbench(){
  const [projects,setProjects]=useState([]),[selected,setSelected]=useState(()=>localStorage.getItem('masa.selected')||'');
  const [detail,setDetail]=useState(null),[view,setView]=useState(null),[profiles,setProfiles]=useState(null);
  const [jobId,setJobId]=useState(()=>localStorage.getItem('masa.job')||''),[job,setJob]=useState(null);
  const [awaitingTracking,setAwaitingTracking]=useState(()=>Boolean(localStorage.getItem('masa.job')));
  const [following,setFollowing]=useState(()=>Boolean(localStorage.getItem('masa.job')));
  const [error,setError]=useState(''),[online,setOnline]=useState(false),[boot,setBoot]=useState(null);
  const select=useCallback(id=>{setFollowing(false);setSelected(id);setDetail(null);setView(null);localStorage.setItem('masa.selected',id);},[]);
  const track=useCallback(j=>{if(j?.job_id&&j.status==='running'){setFollowing(true);setJob(j);setAwaitingTracking(true);setJobId(j.job_id);localStorage.setItem('masa.job',j.job_id);}},[]);
  useEffect(()=>{
    let stopped=false;
    Promise.allSettled([api('/bootstrap'),api('/settings')]).then(([b,p])=>{
      if(stopped)return;
      if(b.status==='fulfilled')setBoot(b.value);
      if(p.status==='fulfilled')setProfiles(p.value);
      const failures=[b,p].filter(r=>r.status==='rejected').map(r=>r.reason.message);
      if(failures.length)setError(failures.join(' · '));
    });
    return()=>{stopped=true;};
  },[]);
  useEffect(()=>{
    let stopped=false,timer;
    async function poll(){
      try{
        // 每个读取独立落下结果；项目视图失败不能吞掉真实任务进度。
        // Save independent reads independently; a broken project view must not hide job progress.
        const [catalogResult,bootResult,jobResult]=await Promise.allSettled([api('/projects'),api('/bootstrap'),jobId?api('/jobs/'+jobId):Promise.resolve(null)]);
        if(stopped)return;
        const failures=[];let discovered=false;
        if(catalogResult.status==='fulfilled')setProjects(catalogResult.value.projects);
        else failures.push(catalogResult.reason.message);
        if(bootResult.status==='fulfilled'){
          const currentBoot=bootResult.value;setBoot(currentBoot);setAwaitingTracking(false);
          if(currentBoot.active_job?.job_id&&currentBoot.active_job.job_id!==jobId){
            const active=currentBoot.active_job;setJobId(active.job_id);localStorage.setItem('masa.job',active.job_id);
            setJob({...active,started:active.started??active.created_at});discovered=true;
            // 只有未选历史时才自动跟随；恢复观察不抢手动选择。
            // Discover a live worker after refresh without stealing a selected historical project.
            if(!selected)setFollowing(true);
          }
        }else failures.push(bootResult.reason.message);
        setOnline([catalogResult,bootResult,jobResult].some(r=>r.status==='fulfilled'&&r.value!==null));
        let target=selected;
        if(jobId&&!discovered){
          if(jobResult.status==='fulfilled'){
            const j={...jobResult.value,job_id:jobId};setJob(j);
            target=pollTarget(selected,j,following);
            if(target!==selected){setSelected(target);setDetail(null);setView(null);localStorage.setItem('masa.selected',target);}
            if(j.status!=='running'){setAwaitingTracking(false);setJobId('');localStorage.removeItem('masa.job');if(j.error)failures.push(j.error);}
          }else{
            failures.push(jobResult.reason.message);
            // 只有明确的不存在才清理；500/断网都不丢失任务身份。
            // Clear tracking only on an explicit missing resource, not a 500 or network failure.
            if(jobResult.reason.status===404){setAwaitingTracking(false);setJobId('');setJob(null);localStorage.removeItem('masa.job');}
          }
        }
        if(target){
          const [d,v]=await Promise.allSettled([api('/runs/'+target),api('/projects/'+target)]);
          if(stopped)return;
          if(d.status==='fulfilled')setDetail(d.value);else failures.push(d.reason.message);
          if(v.status==='fulfilled')setView(v.value);else failures.push(v.reason.message);
        }else{setDetail(null);setView(null);}
        if(failures.length)setError([...new Set(failures)].join(' · '));
      }catch(e){if(!stopped){setError(e.message);setOnline(false);}}
      finally{if(!stopped)timer=setTimeout(poll,900);}
    }
    poll();return()=>{stopped=true;clearTimeout(timer);};
  },[selected,jobId,following]);
  async function start(goal,profile,autoVerify=false){
    setError('');const result=await api('/projects/plan',{goal,api_profile_id:profile,background:true,auto_verify:autoVerify});
    setFollowing(true);setAwaitingTracking(true);setJob({status:'running',phase:'planning',started:Date.now()/1000});setJobId(result.job_id);localStorage.setItem('masa.job',result.job_id);
  }
  return {projects,selected,select,detail,view,profiles,setProfiles,job,track,start,error,setError,online,boot,following,
    // 持久状态不能证明后台线程仍存活。 Persisted phase alone does not imply a live worker.
    working:awaitingTracking||Boolean(boot?.active_job)||Boolean(boot?.active_run)||Boolean(detail?.active)||Boolean(detail?.role_active)};
}
