// 界面只解释后端事实，不创造执行状态。 Interpret backend facts without creating execution state.
export const statusLabel={pending:'待执行',running:'执行中',succeeded:'已完成',failed:'失败',blocked:'等待确认',cancelled:'已取消',paused:'已暂停',created:'准备中',needs_attention:'需检查'};

export function defaultPanel(detail){
  // 成功时读代码，其他情况先处理当前动作。 Show code after success, otherwise the next required action.
  return detail?.run?.data?.project_bundle&&detail.run.status==='succeeded'?'code':'task';
}

export function taskSummary(detail,working=false){
  // 等待、失败与执行分开表达，持久 running 不能证明线程存活。
  // Distinguish waiting, failure and execution; persisted running is not worker liveness.
  const run=detail?.run,plan=run?.data?.project_plan;
  if(!run)return {title:'准备工作区',text:'正在读取项目记录。'};
  if(plan?.status==='waiting_for_input')return {title:'需要你补充需求',text:'回答下面的问题后，继续原来的任务。'};
  if(working)return {title:'任务正在执行',text:'角色响应和真实工具结果会自动更新，不需要反复点击。'};
  if(run.status==='cancelled')return {title:'任务已取消',text:'已有代码与执行证据仍保留在版本记录中。'};
  if(plan&&run.status==='failed')return {title:'模型阶段未完成',text:'检查日志中的响应或契约错误；这个草稿尚未执行 Go 工具验证。'};
  if(run.status==='failed'||run.status==='needs_attention')return {title:'需要处理失败',text:'查看验证或日志，按真实证据修复；旧版本不会被覆盖。'};
  if(plan?.status==='awaiting_review')return {title:plan.kind==='code'?'确认生成的代码':'确认项目方案',text:plan.kind==='code'?'检查文件后批准，再执行真实 Go 验证。':'确认结构和验收标准，再生成代码与测试。'};
  if(plan?.status==='approved')return {title:plan.kind==='code'?'代码已批准':'方案已确认',text:'继续当前任务，系统会复用已保存的审批内容。'};
  if(plan)return {title:'流程已保存',text:'当前没有后台执行；检查记录后使用恢复入口。'};
  if(run.status==='succeeded')return {title:'检查已通过',text:'现在可以查看代码、运行程序，或重新验证。Gate 通过只代表选定检查通过。'};
  return {title:'验证等待继续',text:'从当前任务继续执行，详细结果在验证页。'};
}

export function currentStage(stages=[]){
  // 优先正在执行的角色，避免后面的失败或预告节点遮住活动阶段。
  // Prefer the active role over later failures or placeholder stages.
  return stages.find(s=>s.status==='running')||[...stages].reverse().find(s=>['blocked','failed'].includes(s.status))||[...stages].reverse().find(s=>s.status==='succeeded');
}

export function pollTarget(selected,job,following){
  // 手动看历史时继续观察后台，但不抢回执行中的版本。
  // Keep observing background execution without stealing selection from history browsing.
  return following&&job?.run_id?job.run_id:selected;
}

export function codeReference(data){
  // 方案审批也有 approval_ref，但它不是源码引用。
  // Planning approvals also have references, but they must never be rendered as source files.
  return data?.project_bundle?.approval_ref||(data?.project_plan?.kind==='code'?(data.project_plan.approval_ref||data.project_plan.files_ref):null);
}

export function displayStatus(detail){
  // 用业务等待类型解释暂停，避免把待审批误标成执行中断。
  // Explain business waits so pending approval is not mistaken for interrupted execution.
  const run=detail.run,plan=run.data.project_plan;
  if(['failed','cancelled','needs_attention'].includes(run.status))return statusLabel[run.status];
  if(plan?.status==='waiting_for_input')return '等待回答';
  if(plan?.status==='awaiting_review')return plan.kind==='code'?'等待确认代码':'等待确认方案';
  if(detail.active||detail.role_active)return '执行中';
  if(plan?.status==='approved')return '已确认';
  return statusLabel[run.status]||run.status;
}
