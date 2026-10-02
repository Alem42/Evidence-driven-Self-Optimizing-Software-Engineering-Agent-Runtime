import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultPanel,taskSummary,currentStage,pollTarget,codeReference,displayStatus} from './workspaceState.js';
const detail=(status,data={})=>({run:{status,data}});
test('successful verification opens code; drafts and failures remain actionable',()=>{
  assert.equal(defaultPanel(detail('succeeded',{project_bundle:{approval_ref:'saved'}})),'code');
  assert.equal(defaultPanel(detail('failed',{project_bundle:{}})),'task');
  assert.equal(defaultPanel(detail('paused',{project_plan:{kind:'code',status:'awaiting_review'}})),'task');
});
test('clarification is not described as approval or ordinary execution',()=>{
  assert.equal(taskSummary(detail('paused',{project_plan:{status:'waiting_for_input'}}),true).title,'需要你补充需求');
});
test('persisted phase does not masquerade as an active worker',()=>{
  const saved=detail('running',{project_plan:{status:'generating',kind:'code'}});
  assert.equal(taskSummary(saved,false).title,'流程已保存');
  assert.equal(taskSummary(saved,true).title,'任务正在执行');
});
test('active roles take precedence over failed history and pending future nodes',()=>{
  const running={id:'developer',status:'running'};
  assert.equal(currentStage([running,{id:'old',status:'failed'},{id:'gate',status:'pending'}]),running);
  assert.equal(currentStage([{id:'gate',status:'succeeded'}]).id,'gate');
});
test('history selection survives polling while following still advances live revisions',()=>{
  assert.equal(pollTarget('old',{run_id:'live'},false),'old');
  assert.equal(pollTarget('old',{run_id:'live'},true),'live');
  assert.equal(pollTarget('',{run_id:null},true),'');
});
test('approved plans cannot be mistaken for files, while draft and published bundles can',()=>{
  assert.equal(codeReference({project_plan:{approval_ref:'spec'}}),null);
  assert.equal(codeReference({project_plan:{kind:'code',files_ref:'draft'}}),'draft');
  assert.equal(codeReference({project_bundle:{approval_ref:'snapshot'}}),'snapshot');
});
test('business waits and model-stage failures are not mislabeled as tool failures',()=>{
  assert.equal(displayStatus(detail('paused',{project_plan:{status:'waiting_for_input'}})),'等待回答');
  assert.equal(displayStatus(detail('paused',{project_plan:{kind:'code',status:'awaiting_review'}})),'等待确认代码');
  assert.equal(taskSummary(detail('failed',{project_plan:{kind:'code',status:'failed'}})).title,'模型阶段未完成');
});
