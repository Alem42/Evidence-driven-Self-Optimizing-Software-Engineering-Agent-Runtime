import React,{useEffect,useRef,useState} from 'react';
import {api} from '../../api/client';

const gib=n=>typeof n==='number'?(n/1024**3).toFixed(2)+' GiB':'未知';
const seconds=n=>typeof n==='number'?(n/1e9).toFixed(2)+' s':'未知';

// 管理本地模型与实际 verbose 指标；不提供任意 shell 输入。
// Manage local models and measured verbose counters without arbitrary shell execution.
export function OllamaPanel({close,onSelect,disabled}){
  const [catalog,setCatalog]=useState(null),[selected,setSelected]=useState(''),[detail,setDetail]=useState(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(null),[started,setStarted]=useState(null),[now,setNow]=useState(Date.now());
  const [hardware,setHardware]=useState(null),[hardwareBusy,setHardwareBusy]=useState(false),[hardwareError,setHardwareError]=useState('');
  const [diagnostics,setDiagnostics]=useState(null),[diagnosticsBusy,setDiagnosticsBusy]=useState(false),[diagnosticsError,setDiagnosticsError]=useState(''),[copied,setCopied]=useState(false);
  const panel=useRef(null),busyRef=useRef(false);busyRef.current=busy;
  useEffect(()=>{
    // 操作期间不能隐藏未知请求；键盘焦点留在控制页。
    // Keep focus in the controller and never hide an in-flight request with Escape.
    const previous=document.activeElement;panel.current?.querySelector('button')?.focus();
    function key(e){if(e.key==='Escape'&&!busyRef.current){e.preventDefault();close();}if(e.key!=='Tab')return;
      const items=[...panel.current.querySelectorAll('button:not(:disabled),summary')].filter(n=>n.getClientRects().length);
      if(!items.length)return;const first=items[0],last=items.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
    document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus();};
  },[]);
  useEffect(()=>{refresh();const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  async function refresh(){try{const c=await api('/ollama');setCatalog(c);setSelected(old=>c.models.some(m=>m.name===old)?old:c.models.find(m=>m.name.startsWith('glm-4.7-flash'))?.name||c.models[0]?.name||'');setError('');}catch(e){setError(e.message);}}
  async function readHardware(){
    // 只读系统摘要由后端固定命令采集，不进入模型调用，也不请求提权。
    // Read bounded hardware facts without model calls or elevation.
    setHardwareBusy(true);setHardwareError('');try{setHardware(await api('/hardware'));}catch(e){setHardwareError(e.message);}finally{setHardwareBusy(false);}
  }
  async function readDiagnostics(){
    // 按需读结构化后台异常，不自动刷新或展示原始请求正文。
    // Read structured backend diagnostics on demand without polling or raw request bodies.
    setDiagnosticsBusy(true);setDiagnosticsError('');setCopied(false);try{setDiagnostics(await api('/diagnostics'));}catch(e){setDiagnosticsError(e.message);}finally{setDiagnosticsBusy(false);}
  }
  async function copyDiagnostics(){try{await navigator.clipboard.writeText(JSON.stringify(diagnostics,null,2));setCopied(true);}catch{setDiagnosticsError('无法自动复制，请从下方文本中选择并复制。');}}
  async function act(action){setBusy(true);setError('');setResult(null);setStarted(Date.now());try{
    let r=await api('/ollama/action',{action,model:selected});
    if(r.job_id){while(true){const job=await api('/jobs/'+r.job_id);if(job.status==='failed'||job.status==='interrupted')throw new Error(job.error||'操作中断，请检查服务状态，不自动重试');if(job.status==='completed'){r=job.result;break;}await new Promise(resolve=>setTimeout(resolve,900));}}
    if(action==='show')setDetail(r);else if(action==='select'){onSelect(r.settings,r.settings.active_id);setResult({message:'已设为项目默认模型；正在执行的任务保持原模型。'});}else setResult(r);
    await refresh();
  }catch(e){setError(e.message);}finally{setBusy(false);setStarted(null);}}
  const model=catalog?.models.find(m=>m.name===selected),running=catalog?.running.find(m=>m.name===selected);
  const metrics=result?.metrics;
  return <div className="overlay"><section ref={panel} className="modal inspector ollama-controller" role="dialog" aria-modal="true" aria-label="Ollama 控制器">
    <div className="panel-head"><h2>本地模型 · Ollama</h2><button disabled={busy} onClick={close}>关闭</button></div>
    <div className="action-bar"><span>{catalog?'已连接 · Ollama '+catalog.version:'连接本地服务…'}</span><button disabled={busy} onClick={refresh}>刷新列表 / ps</button></div>
    <p className="hint">固定连接 127.0.0.1:11434。选择模型用于新任务，加载/释放影响本机 Ollama 内存；正在执行的项目不会切换。</p>
    {disabled&&<p className="notice">项目正在运行：仍可查看模型、硬件与诊断；切换、加载、释放和测速将在任务结束后恢复。</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    <div className="api-layout"><nav className="api-list" aria-label="本地模型列表">{catalog?.models.map(m=><button key={m.name} disabled={busy} className={'run-item '+(selected===m.name?'current':'')} onClick={()=>{setSelected(m.name);setDetail(null);setResult(null);}}><strong>{m.name}</strong><small>{gib(m.size)} · {m.details?.parameter_size} · {m.details?.quantization_level}</small><small>{catalog.running.some(r=>r.name===m.name)?'已加载':'未加载'}</small></button>)}</nav>
    <div>{model&&<><h3>{selected}</h3><dl className="model-facts"><dt>磁盘大小</dt><dd>{gib(model.size)}</dd><dt>参数 / 量化</dt><dd>{model.details?.parameter_size} / {model.details?.quantization_level}</dd><dt>当前模型内存 / 显存</dt><dd>{running?gib(running.size)+' / '+gib(running.size_vram):'未加载'}</dd><dt>当前加载上下文</dt><dd>{running?.context_length??'未加载'}</dd><dt>权重摘要</dt><dd><code>{model.digest}</code></dd></dl>
      <div className="actions"><button disabled={busy||disabled} className="primary" onClick={()=>act('select')}>用于项目 / 切换默认</button><button disabled={busy} onClick={()=>act('show')}>模型详情</button><button disabled={busy||disabled} onClick={()=>act('load')}>加载</button><button disabled={busy||disabled} onClick={()=>act('unload')}>释放内存</button><button disabled={busy||disabled} onClick={()=>act('test')}>真实调用测速</button></div>
      {busy&&<div className="notice" role="status"><progress/> 操作中 · {Math.floor((now-started)/1000)} 秒；不自动重试</div>}
      {result?.message&&<p className="notice">{result.message}</p>}
      {metrics&&<div className="notice"><strong>本次真实推理：{metrics.generation_tokens_per_second??'未知'} tokens/s</strong><p>提示处理 {metrics.prompt_tokens_per_second??'未知'} tokens/s · 总耗时 {seconds(metrics.total_duration)} · 加载 {seconds(metrics.load_duration)}</p><p>输入 {metrics.prompt_eval_count??'未知'} tokens · 输出 {metrics.eval_count??'未知'} tokens · 生成耗时 {seconds(metrics.eval_duration)}</p></div>}
      {detail&&<details open><summary>模型信息 / show</summary><pre className="local-details">{JSON.stringify(detail,null,2)}</pre></details>}
      {result&&<details><summary>完整操作结果与 verbose 计数</summary><pre className="local-details">{JSON.stringify(result,null,2)}</pre></details>}
      <details><summary>对应命令与当前边界</summary><pre>ollama list{'\n'}ollama ps{'\n'}ollama show {selected}{'\n'}ollama run {selected} --verbose{'\n'}ollama stop {selected}</pre><p>页面通过官方接口执行对应动作。暂不提供 pull/rm/create、任意终端或后台服务重启；下载和删除仍在本地终端执行。</p></details>
    </>}</div></div>
    <details onToggle={e=>{if(e.currentTarget.open&&!hardware&&!hardwareBusy)readHardware();}}>
      <summary>硬件与资源 · 显卡 / 内存</summary>
      <div className="action-bar"><button disabled={hardwareBusy} onClick={readHardware}>{hardwareBusy?'读取中…':'刷新硬件摘要'}</button>{hardware&&<span className="hint">最近采集 {new Date(hardware.collected_at).toLocaleTimeString()} · 后端缓存 {hardware.cache_seconds} 秒</span>}</div>
      <p className="hint">普通只读查询，不需要管理员权限。显存占用是采集瞬间的结果；内存频率为固件报告值，不是实时测速。</p>
      {hardwareError&&<p className="error" role="alert">{hardwareError}</p>}
      {hardware&&<>
        <h3>显卡</h3>
        {!hardware.gpu.available&&<p className="notice">暂时无法读取显卡信息；可继续使用已配置的模型。{hardware.gpu.warning&&' 诊断：'+hardware.gpu.warning}</p>}
        {hardware.gpu.devices.map(g=><dl className="model-facts" key={g.index}><dt>显卡 {g.index}</dt><dd>{g.name}</dd><dt>总显存 / 已用 / 空闲</dt><dd>{[g.total_mib,g.used_mib,g.free_mib].map(n=>typeof n==='number'?(n/1024).toFixed(2)+' GiB':'未知').join(' / ')}</dd><dt>GPU 利用率</dt><dd>{g.utilization_percent==null?'未知':g.utilization_percent+'%'}</dd></dl>)}
        {hardware.gpu.available&&hardware.gpu.warning&&<p className="hint">部分显卡数据未提供：{hardware.gpu.warning}</p>}
        <h3>系统内存</h3><dl className="model-facts"><dt>物理内存总量</dt><dd>{gib(hardware.memory.total_bytes)}</dd></dl>
        {!hardware.memory.available&&<p className="notice">暂时无法读取内存信息。{hardware.memory.warning&&' 诊断：'+hardware.memory.warning}</p>}
        {hardware.memory.modules.map((m,i)=><dl className="model-facts" key={i}><dt>内存模块 {i+1}</dt><dd>{gib(m.capacity_bytes)}</dd><dt>配置频率 / 标称频率</dt><dd>{[m.configured_speed_mhz,m.speed_mhz].map(n=>n==null?'未知':n+' MHz').join(' / ')}</dd></dl>)}
        {hardware.memory.available&&hardware.memory.warning&&<p className="hint">部分内存数据未提供：{hardware.memory.warning}</p>}
      </>}
    </details>
    <details onToggle={e=>{if(e.currentTarget.open&&!diagnostics&&!diagnosticsBusy)readDiagnostics();}}>
      <summary>后台诊断 · 异常记录</summary>
      <p className="hint">这里记录后台请求的异常类型和代码位置；项目业务失败证据仍在项目日志中。记录不包含需求正文和密钥。</p>
      <div className="actions"><button disabled={diagnosticsBusy} onClick={readDiagnostics}>{diagnosticsBusy?'读取中…':'读取后台诊断'}</button>{diagnostics&&<button onClick={copyDiagnostics}>{copied?'已复制':'复制诊断 JSON'}</button>}</div>
      {diagnosticsError&&<p className="error" role="alert">{diagnosticsError}</p>}
      {diagnostics&&<><p>{diagnostics.errors.length?'最近 '+diagnostics.errors.length+' 条后台异常':'当前没有记录到后台请求异常。'}</p><pre className="local-details">{JSON.stringify(diagnostics,null,2)}</pre></>}
    </details>
  </section></div>;
}
