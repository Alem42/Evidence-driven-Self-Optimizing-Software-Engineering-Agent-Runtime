import React,{useState} from 'react';
import {api} from '../../api/client';
import {projectJob} from './progress';

// 一行一个参数，避免 shell 解析；应用结果与测试结论分开。
// Each line is one argument; application output is independent of verification.
export function ApplicationPanel({detail}){
  const [args,setArgs]=useState(''),[busy,setBusy]=useState(false),[result,setResult]=useState(null),[error,setError]=useState('');
  async function run(){setBusy(true);setError('');setResult(null);try{
    const r=await projectJob('/runs/'+detail.run.id+'/run-application',{argv:args===''?[]:args.split('\n')},()=>{});
    setResult(r.app_result);
  }catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="panel"><h3>运行这个程序</h3><label>程序参数（每行一个；留空表示无参数）<textarea rows={3} value={args} onChange={e=>setArgs(e.target.value)} disabled={busy}/></label>
    <div className="action-bar"><button className="primary" disabled={busy} onClick={run}>{busy?'正在构建并运行…':'运行程序'}</button>{busy&&<button onClick={()=>api('/runs/'+detail.run.id+'/stop-application',{}).catch(e=>setError(e.message))}>停止</button>}</div>
    <p className="muted">最多运行 30 秒；不会改变已有测试和 Gate 结论。历史输出保存在执行日志。</p>
    {error&&<p className="error">{error}</p>}{result&&<><p>{result.phase==='build'?'构建':'运行'} · {result.status} · 退出码 {result.exit_code??'未知'}</p><h4>标准输出</h4><pre>{result.stdout||'（空）'}</pre><h4>错误输出</h4><pre>{result.stderr||result.error||'（空）'}</pre>{result.truncated&&<p>输出超过上限，已截断。</p>}</>}
  </section>;
}
