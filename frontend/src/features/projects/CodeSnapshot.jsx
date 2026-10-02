import React,{useEffect,useState} from 'react';
import {api} from '../../api/client';
import {codeReference} from './workspaceState';

// 草稿与批准快照使用同一个文件浏览器；不读取可变工作区。
// Browse drafts and approved snapshots in one viewer, never reading mutable workspace files.
export function CodeSnapshot({detail}){
  const plan=detail.run.data.project_plan;
  const ref=codeReference(detail.run.data);
  const [files,setFiles]=useState(null),[path,setPath]=useState(''),[error,setError]=useState('');
  useEffect(()=>{let stopped=false;setFiles(null);setError('');if(!ref)return;
    api('/runs/'+detail.run.id+'/artifacts/'+ref).then(r=>{if(stopped)return;
      const found=r.artifact.files||r.artifact;setFiles(found);
      setPath(previous=>Object.hasOwn(found,previous)?previous:found['cmd/app/main.go']?'cmd/app/main.go':Object.keys(found).find(p=>p.endsWith('.go')&&!p.endsWith('_test.go'))||Object.keys(found)[0]);
    }).catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;};
  },[detail.run.id,ref]);
  if(!ref)return <div className="empty-document"><h2>还没有生成代码</h2><p>先完成方案，再生成项目。文件会在模型完整响应通过校验后显示，不会展示未经校验的部分 JSON。</p></div>;
  return <section className="file-browser" aria-label="项目代码"><div className="file-browser-heading"><strong>{plan?'代码草稿':'已批准快照'}</strong><span className="muted">只读 · 审核与编辑在当前任务</span></div>
    {error&&<p className="error">{error}</p>}{!files&&!error&&<p role="status">正在读取文件…</p>}
    {files&&<div className="file-browser-body"><nav className="file-tree" aria-label="项目文件">{Object.keys(files).sort().map(p=><button key={p} className={path===p?'current':''} aria-current={path===p?'page':undefined} onClick={()=>setPath(p)}><span aria-hidden="true">{p.endsWith('_test.go')?'◇':'◻'}</span>{p}</button>)}</nav>
      <div className="file-source"><div className="file-path"><code>{path}</code><span>{files[path]?.split('\n').length||0} 行</span></div><pre className="source-code">{files[path]}</pre></div></div>}
  </section>;
}
