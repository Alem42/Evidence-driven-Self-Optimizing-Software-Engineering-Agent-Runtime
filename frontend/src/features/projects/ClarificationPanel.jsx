import React,{useState} from 'react';
import {projectJob} from './progress';

// 回答业务问题而非批准方案；不预选、不自动提交。 Answer requirements without implicit approval or default selection.
export function ClarificationPanel({detail,profiles,select,onProgress}){
  const plan=detail.run.data.project_plan;
  const [answers,setAnswers]=useState({}),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(){
    setBusy(true);setError('');
    try{const r=await projectJob('/runs/'+detail.run.id+'/answer-clarification',{
      question_id:plan.clarification_id,answers,api_profile_id:profiles?.active_id},onProgress);select(r.id);}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <section className="panel"><h2>Planner 需要你补充需求</h2><p>{plan.clarification.reason}</p>
    {plan.clarification.questions.map(q=><fieldset key={q.key} disabled={busy}><legend>{q.title}</legend>
      {q.options.map(o=><label key={o.id}><input type="radio" name={q.key} checked={answers[q.key]?.option_id===o.id} onChange={()=>setAnswers({...answers,[q.key]:{option_id:o.id}})}/>{o.label}</label>)}
      <label>自行填写<textarea maxLength={2000} value={answers[q.key]?.text||''} onChange={e=>setAnswers({...answers,[q.key]:{text:e.target.value}})}/></label>
    </fieldset>)}
    {error&&<p className="error" role="alert">{error}</p>}
    <button disabled={busy} onClick={submit}>{busy?'正在继续规划…':'提交回答并继续'}</button>
    <p className="hint">回答用于明确需求，不代表批准方案。等待期间不会调用模型。</p>
  </section>;
}
