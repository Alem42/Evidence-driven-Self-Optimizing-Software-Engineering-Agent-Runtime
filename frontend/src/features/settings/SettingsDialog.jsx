import React, {useEffect,useRef,useState} from 'react';
import {api} from '../../api/client';
import {normalizeProfiles, parseProfile,profileReady,profileLabel} from './profiles';

const defaults={name:'DeepSeek V4 Pro',base_url:'https://api.deepseek.com',model:'deepseek-v4-pro',api_key:'',timeout_seconds:60,max_output_tokens:8192,token_parameter:'max_tokens',thinking:'disabled',model_type:'cloud',protocol:'openai',enabled:true,level:2,priority:0,context_limit:32768,roles:[]};
const roleNames={project_planner:'Planner',project_tester:'Tester',project_developer:'Developer',project_repair:'实现修复',project_test_revision:'测试修订',project_test_reviewer:'语义评审',verifier:'工具验证',code_generation:'旧单文件生成'};

// 管理会话密钥和配置；兼容检查先于渲染列表。 Manage profiles and session keys after checking backend compatibility.
export function SettingsDialog({initial,close,onUpdate}) {
  const first=normalizeProfiles(initial);
  const [catalog,setCatalog]=useState(first);
  const [values,setValues]=useState({...defaults,...first.profiles.find(p=>p.id===first.active_id),api_key:'',...(!first.profiles.length?{new:true}:{})});
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [message,setMessage]=useState(''), [importText,setImportText]=useState('');
  const dialog=useRef(null),busyRef=useRef(busy);busyRef.current=busy;
  useEffect(()=>{
    // 弹窗内循环键盘焦点，关闭后返回入口；请求期间禁止 Escape 中断可见操作。
    // Contain keyboard focus and restore its origin; Escape never hides an in-flight operation.
    const previous=document.activeElement;
    dialog.current?.querySelector('button')?.focus();
    function keydown(e){
      if(e.key==='Escape'&&!busyRef.current){e.preventDefault();close();}
      if(e.key!=='Tab')return;
      const controls=[...dialog.current.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary')].filter(n=>n.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
    document.addEventListener('keydown',keydown);
    return()=>{document.removeEventListener('keydown',keydown);previous?.focus();};
  },[]);
  // 更新后只回填非秘密字段。 Refresh only public fields after a mutation.
  async function save(body) {
    setBusy(true);setError('');setMessage('');
    try {const result=normalizeProfiles(await api('/settings',body));setCatalog(result);onUpdate(result);
      setValues(result.profiles.length?{...defaults,...result.profiles.find(p=>p.id===result.active_id),api_key:''}:{...defaults,new:true});
      setMessage(result.key_storage==='local_file'?'已保存到本地配置，重启后可继续使用。':'已保存。密钥在当前服务内存。');
    } catch(e){setError(e.message);} finally{setBusy(false);}
  }
  // 测试保存后的参数，一次点击只调用一次模型。 Probe saved parameters with one real request per click.
  async function test(){setBusy(true);setError('');setMessage('连接测试中…');try{const r=await api('/settings/test',{id:values.id});setMessage(r.message+(r.usage?' · '+r.usage.total_tokens+' tokens':''));const next=normalizeProfiles(await api('/settings'));setCatalog(next);onUpdate(next);}catch(e){setError(e.message);}finally{setBusy(false);}}
  // JSON 解析失败保留输入供修正，成功后清空包含秘密的导入框。 Clear secret-bearing import text after successful parsing.
  function importProfile(){try{const parsed=parseProfile(importText);setValues({...defaults,...(parsed.model_type==='local'?{protocol:'ollama',timeout_seconds:180,level:1,context_limit:8192}:{}),...parsed,new:true});setImportText('');setError('');setMessage('已填入表单，请检查后保存。');}catch(e){setError(e.message);}}
  return <div className="overlay"><section ref={dialog} className="modal inspector" role="dialog" aria-modal="true" aria-label="模型管理">
    <div className="panel-head"><h2>模型管理 · 本地与云端</h2><button disabled={busy} onClick={close}>关闭</button></div>
    {!catalog.compatible?<p className="error">后端版本过旧，无法管理 API。请重启 MASA 服务并刷新页面，不要继续使用旧的 8765/8766 进程。</p>:<div className="api-layout">
      <div className="api-list"><button disabled={busy} onClick={()=>{setValues({...defaults,new:true});setError('');setMessage('');}}>＋ 添加模型</button>
        {catalog.profiles.map(p=><button key={p.id} disabled={busy} className={'run-item '+(values.id===p.id?'current':'')} onClick={()=>{setValues({...defaults,...p,api_key:''});setError('');setMessage('');}}><strong>{profileLabel(p)}</strong><small>{p.model} · 优先级 {p.priority??0}</small><small>{p.id===catalog.active_id?'默认 · ':''}{p.enabled===false?'已禁用':profileReady(p)?'可调用':'缺少密钥'}</small><small>{p.last_test?.ok?'测试通过':p.last_test?'测试失败':'未测试'}</small></button>)}
      </div>
      <form onSubmit={e=>{e.preventDefault();save(values);}}>
        <div className="field-pair"><label>模型类型<select value={values.model_type} onChange={e=>{const local=e.target.value==='local';setValues({...values,model_type:e.target.value,protocol:local?'ollama':'openai',base_url:local?'http://127.0.0.1:11434':'https://api.deepseek.com',model:local?'':'deepseek-v4-pro',name:local?'Ollama 本地模型':'DeepSeek V4 Pro',level:local?1:2,timeout_seconds:local?180:60,context_limit:local?8192:32768,api_key:''});}}><option value="cloud">云端 API</option><option value="local">本地 Ollama / 兼容服务</option></select></label><label>传输协议<select value={values.protocol} onChange={e=>setValues({...values,protocol:e.target.value})}><option value="openai">OpenAI compatible（地址通常含 /v1）</option>{values.model_type==='local'&&<option value="ollama">Ollama 原生（地址不含 /v1）</option>}</select></label></div>
        <label className="check"><input type="checkbox" checked={values.enabled} onChange={e=>setValues({...values,enabled:e.target.checked})}/> 启用此模型</label>
        <details><summary>从 JSON 导入一组 API</summary><textarea rows={4} value={importText} onChange={e=>setImportText(e.target.value)} placeholder={'{"base_url":"https://api.deepseek.com","model":"deepseek-v4-pro","api_key":"…"}'}/><button type="button" disabled={busy||!importText.trim()} onClick={importProfile}>解析并填入表单</button></details>
        <button type="button" disabled={busy} onClick={()=>setValues({...values,...defaults,api_key:values.api_key})}>使用 DeepSeek 推荐参数</button>
        {values.model_type==='cloud'&&<><div className="notice">密钥不回显。可选择保存到本地忽略文件，重启后继续使用；清除密钥会同步清除本地保存。修改后先保存，再测试连接。</div>
        <label className="check"><input type="checkbox" checked={catalog.key_storage==='local_file'||Boolean(values.persist_key)} disabled={catalog.key_storage==='local_file'} onChange={e=>setValues({...values,persist_key:e.target.checked})}/> 在本地持久保存密钥（不提交 Git）</label></>}
        {(values.model_type==='local'?['name','base_url','model']:['name','base_url','model','api_key']).map(key=><label key={key}>{{name:'配置名称',base_url:'Base URL',model:'模型 ID（Ollama 填完整 tag）',api_key:'API Key'}[key]}<input required={key!=='api_key'} type={key==='api_key'?'password':'text'} autoComplete="off" value={values[key]||''} placeholder={key==='api_key'?'留空保留已有密钥':key==='model'&&values.model_type==='local'?'例如 gemma4:12b':''} onChange={e=>setValues({...values,[key]:e.target.value})}/></label>)}
        <div className="field-pair"><label>能力等级（越大越强）<input type="number" min="1" max="100" value={values.level} onChange={e=>setValues({...values,level:Number(e.target.value)})}/></label><label>同等级优先级（越小越先）<input type="number" min="0" max="10000" value={values.priority} onChange={e=>setValues({...values,priority:Number(e.target.value)})}/></label></div><p className="hint">等级由你指定，不是自动能力评测。目前仍是固定模型选择，尚不自动升级。</p>
        <div className="field-pair"><label>上下文限制（token）<input type="number" min="512" max="262144" value={values.context_limit} onChange={e=>setValues({...values,context_limit:Number(e.target.value)})}/></label><label>请求超时（秒）<input type="number" min="1" max={values.model_type==='local'?600:60} value={values.timeout_seconds} onChange={e=>setValues({...values,timeout_seconds:Number(e.target.value)})}/></label></div>
        <label>输出 token 上限<input type="number" min="64" max="8192" value={values.max_output_tokens} onChange={e=>setValues({...values,max_output_tokens:Number(e.target.value)})}/></label>
        <details><summary>可承担角色与可选价格</summary><p className="hint">不选角色表示全部允许。价格为人民币/百万 token，仅保存配置，尚不计算任务费用。</p>{Object.entries(roleNames).map(([key,label])=><label className="check" key={key}><input type="checkbox" checked={values.roles.includes(key)} onChange={e=>setValues({...values,roles:e.target.checked?[...values.roles,key]:values.roles.filter(r=>r!==key)})}/>{label}</label>)}<div className="field-pair">{['input_price_per_million','output_price_per_million'].map((key,i)=><label key={key}>{i?'输出单价':'输入单价'}<input type="number" min="0" step="any" value={values[key]??''} onChange={e=>setValues({...values,[key]:e.target.value===''?null:Number(e.target.value)})}/></label>)}</div></details>
        <details><summary>高级调用参数</summary><div className="field-pair"><label>输出参数<select value={values.token_parameter} onChange={e=>setValues({...values,token_parameter:e.target.value})}><option value="max_tokens">max_tokens · DeepSeek</option><option value="max_completion_tokens">max_completion_tokens</option></select></label><label>思考模式<select value={values.thinking} onChange={e=>setValues({...values,thinking:e.target.value})}><option value="disabled">关闭</option><option value="enabled">开启</option><option value="auto">提供商默认</option></select></label></div></details>
        {message&&<p className="notice" role="status">{message}</p>}{error&&<p className="error" role="alert">{error}</p>}
        <div className="actions"><button className="primary" disabled={busy}>保存配置</button><button type="button" disabled={busy||!values.id} onClick={test}>测试连接（真实调用）</button><button type="button" disabled={busy||!values.id} onClick={()=>save({action:'select',id:values.id})}>设为默认</button></div><details><summary>配置维护</summary><div className="actions">{values.model_type==='cloud'&&<button type="button" disabled={busy||!values.id} onClick={()=>save({id:values.id,clear_key:true})}>清除密钥</button>}<button type="button" disabled={busy||!values.id} onClick={()=>save({action:'delete',id:values.id})}>删除配置</button></div></details>
      </form>
    </div>}</section></div>;
}
