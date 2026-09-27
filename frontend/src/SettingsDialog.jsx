import React, {useState} from 'react';
import {api} from './api';
import {normalizeProfiles, parseProfile} from './apiProfiles';

const defaults={name:'DeepSeek V4 Pro',base_url:'https://api.deepseek.com',model:'deepseek-v4-pro',api_key:'',timeout_seconds:60,max_output_tokens:4096,token_parameter:'max_tokens',thinking:'disabled'};

// 管理会话密钥和配置；兼容检查先于渲染列表。 Manage profiles and session keys after checking backend compatibility.
export function SettingsDialog({initial,close,onUpdate}) {
  const first=normalizeProfiles(initial);
  const [catalog,setCatalog]=useState(first);
  const [values,setValues]=useState({...defaults,...first.profiles.find(p=>p.id===first.active_id),api_key:'',...(!first.profiles.length?{new:true}:{})});
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [message,setMessage]=useState(''), [importText,setImportText]=useState('');
  // 更新后只回填非秘密字段。 Refresh only public fields after a mutation.
  async function save(body) {
    setBusy(true);setError('');setMessage('');
    try {const result=normalizeProfiles(await api('/settings',body));setCatalog(result);onUpdate(result);
      setValues(result.profiles.length?{...defaults,...result.profiles.find(p=>p.id===result.active_id),api_key:''}:{...defaults,new:true});
      setMessage('已保存。密钥仅在当前服务内存，重启后需重新输入。');
    } catch(e){setError(e.message);} finally{setBusy(false);}
  }
  // 测试保存后的参数，一次点击只调用一次模型。 Probe saved parameters with one billed request per click.
  async function test(){setBusy(true);setError('');setMessage('连接测试中…');try{const r=await api('/settings/test',{id:values.id});setMessage(r.message+(r.usage?' · '+r.usage.total_tokens+' tokens':''));const next=normalizeProfiles(await api('/settings'));setCatalog(next);onUpdate(next);}catch(e){setError(e.message);}finally{setBusy(false);}}
  // JSON 解析失败保留输入供修正，成功后清空包含秘密的导入框。 Clear secret-bearing import text after successful parsing.
  function importProfile(){try{setValues({...defaults,...parseProfile(importText),new:true});setImportText('');setError('');setMessage('已填入表单，请检查后保存。');}catch(e){setError(e.message);}}
  return <div className="overlay"><section className="modal inspector" role="dialog" aria-modal="true" aria-label="API 管理">
    <div className="panel-head"><h2>API 管理</h2><button disabled={busy} onClick={close}>关闭</button></div>
    {!catalog.compatible?<p className="error">后端版本过旧，无法管理 API。请重启 MASA 服务并刷新页面，不要继续使用旧的 8765/8766 进程。</p>:<div className="api-layout">
      <div className="api-list"><button disabled={busy} onClick={()=>{setValues({...defaults,new:true});setError('');setMessage('');}}>＋ 添加 API</button>
        {catalog.profiles.map(p=><button key={p.id} disabled={busy} className={'run-item '+(values.id===p.id?'current':'')} onClick={()=>{setValues({...defaults,...p,api_key:''});setError('');setMessage('');}}><strong>{p.name || p.model}</strong><small>{p.model}</small><small>{p.id===catalog.active_id?'默认 · ':''}{p.key_configured?'密钥已就绪':'需重新输入密钥'}</small><small>{p.last_test?.ok?'测试通过':p.last_test?'测试失败':'未测试'}</small></button>)}
      </div>
      <form onSubmit={e=>{e.preventDefault();save(values);}}>
        <details><summary>从 JSON 导入一组 API</summary><textarea rows={4} value={importText} onChange={e=>setImportText(e.target.value)} placeholder={'{"base_url":"https://api.deepseek.com","model":"deepseek-v4-pro","api_key":"…"}'}/><button type="button" disabled={busy||!importText.trim()} onClick={importProfile}>解析并填入表单</button></details>
        <button type="button" disabled={busy} onClick={()=>setValues({...values,...defaults,api_key:values.api_key})}>使用 DeepSeek 推荐参数</button>
        <div className="notice">名称和模型参数会保存；密钥不回显、不写磁盘。新服务启动后需重新输入。修改后先保存，再测试连接。</div>
        {['name','base_url','model','api_key'].map((key,i)=><label key={key}>{['配置名称','Base URL（可粘贴完整 Chat Completions 地址）','模型 ID','API Key'][i]}<input required={key!=='api_key'} type={key==='api_key'?'password':'text'} autoComplete="off" value={values[key]||''} placeholder={key==='api_key'?'留空保留当前会话已有密钥':''} onChange={e=>setValues({...values,[key]:e.target.value})}/></label>)}
        <div className="field-pair"><label>请求超时（秒）<input type="number" min="1" max="60" value={values.timeout_seconds} onChange={e=>setValues({...values,timeout_seconds:Number(e.target.value)})}/></label><label>输出 token 上限<input type="number" min="64" max="8192" value={values.max_output_tokens} onChange={e=>setValues({...values,max_output_tokens:Number(e.target.value)})}/></label></div>
        <div className="field-pair"><label>输出参数<select value={values.token_parameter} onChange={e=>setValues({...values,token_parameter:e.target.value})}><option value="max_tokens">max_tokens · DeepSeek</option><option value="max_completion_tokens">max_completion_tokens</option></select></label><label>思考模式<select value={values.thinking} onChange={e=>setValues({...values,thinking:e.target.value})}><option value="disabled">关闭</option><option value="enabled">开启</option><option value="auto">提供商默认</option></select></label></div>
        {message&&<p className="notice" role="status">{message}</p>}{error&&<p className="error" role="alert">{error}</p>}
        <div className="actions"><button className="primary" disabled={busy}>保存配置</button><button type="button" disabled={busy||!values.id} onClick={test}>测试已保存配置</button><button type="button" disabled={busy||!values.id} onClick={()=>save({action:'select',id:values.id})}>设为默认</button><button type="button" disabled={busy||!values.id} onClick={()=>save({id:values.id,clear_key:true})}>清除密钥</button><button type="button" disabled={busy||!values.id} onClick={()=>save({action:'delete',id:values.id})}>删除配置</button></div>
      </form>
    </div>}</section></div>;
}
