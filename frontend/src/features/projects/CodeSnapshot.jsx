import React,{useEffect,useState} from 'react';
import {api} from '../../api/client';

// 从当前验证版本的批准引用加载只读代码，不读取可能改变的工作区文件。
// Render the exact approved files for this verification version, never mutable workspace reads.
export function CodeSnapshot({detail}){
  const ref=detail.run.data.project_bundle?.approval_ref;
  const [files,setFiles]=useState(null),[path,setPath]=useState(''),[error,setError]=useState('');
  useEffect(()=>{let stopped=false;setFiles(null);setError('');if(!ref)return;
    api('/runs/'+detail.run.id+'/artifacts/'+ref).then(r=>{if(stopped)return;const found=r.artifact.files;
      setFiles(found);setPath(found['cmd/app/main.go']?'cmd/app/main.go':Object.keys(found)[0]);
    }).catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;};},[detail.run.id,ref]);
  return <section className="panel code-snapshot"><div className="section-heading"><h3>本次验证的代码</h3><span className="muted">来自已批准的文件快照</span></div>
    {error&&<p className="error">{error}</p>}
    {files&&<><label>文件<select value={path} onChange={e=>setPath(e.target.value)}>{Object.keys(files).map(p=><option key={p}>{p}</option>)}</select></label><pre className="source-code">{files[path]}</pre></>}
  </section>;
}
