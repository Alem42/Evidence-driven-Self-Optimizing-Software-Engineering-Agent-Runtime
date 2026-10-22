"""验证失败之后的修复子图：诊断、断言裁判、修复与修订测试的编排。 What happens after a failed verification: the diagnosis, the assertion referee and the orchestration of repairs and test revisions."""
from masa.application.checks.check_policy import format_only, test_revision_needed
from masa.application.orchestration.router import RoutingStop
from masa.application.orchestration import attempts
from masa.application.checks import ownership
from masa.application.orchestration.flow import FlowEngine
from masa.application.orchestration.workflows import FIX_V1
from masa.runtime.roles import RoleRuntime
from masa.domain.models import CallCancelled, ExpectationSuspected, MasaError, TransportFailure
from masa.application.orchestration.limits import NoChange, NoProgress, REWRITE_AFTER_STALLS, MAX_REWRITES
import time
from masa.application.checks import referee


class FixFlowMixin:
    # ───────────── 修复子图（声明式工作流）/ the repair subgraph (declarative workflow) ─────────────
    def _trace_step(self, step, verified):
        """把工作流走过的每一步写进事件，报告里可见。 Persist every workflow step as an event for the report."""
        self.store.event(verified,'workflow_node',{'workflow':f"{FIX_V1['id']}-v{FIX_V1['version']}",'node':step.node,'action':step.action,'to':step.to,'why':step.why})

    def _run_fix_flow(self, verified, checks, attempt, generation, assertion, repeats, analysis):
        """用 fix-v1 决定并执行“验证失败之后做什么”。返回 {end, outcome, facts, trace}。
        Decide and run what happens after a failed verification, using the fix-v1 workflow."""
        store=self.store
        phase=self._phase
        outputs=[result for _,result in checks]
        evidence='\n'.join(str(o.get('stdout',''))+'\n'+str(o.get('stderr','')) for o in outputs)
        # 修复阶段的输入规模：当前整套代码 + 失败证据（估算，用于上下文准入）。
        # Input size of a fix call: the current bundle plus failure evidence (an estimate for context admission).
        bundle=store.read(store.run(verified)['data']['project_bundle']['approval_ref'])['files']
        need=[bundle,evidence[:20000]]
        self.job.pop('referee_text',None)
        facts={'verified':verified,'draft':None,'halted':False,'noop':False,'noop_count':0,'diagnosed':False,'diagnosis':None,'analysis':analysis,
               # 工人修复（v2repair）连续“卡住且没有改善”两次，就是同样的信息再来一遍：第三次不会有新东西，转去修订测试（真实 LCS 复跑：3 次卡住 ×3 步后才转，又多花约 1/5 的 token）。V1 默认流程仍是 3 次。
               # A worker repair (v2repair) that got stuck without improvement twice is the same information again: a third try brings nothing new, revise the tests (a real LCS rerun spent three stuck attempts of three steps before flipping, about a fifth of its tokens). The V1 default flow keeps 3.
               'noop_limit':2 if self.job['request'].get('runtime')=='v2repair' else 3}

        def classify(ctx):
            primary=analysis['primary']
            if primary=='ambiguous' and test_revision_needed(evidence,checks,self.pack):
                primary='test'  # 旧规则仍作为兜底 / the legacy rule remains as a fallback
            history=len(self._history('fix'))
            noop=int(self.job.get('noop_streak',0))
            diagnoses=int(self.job.get('diagnoses',0))
            # 只有断言失败（没有编译/准备错误）时，先让 Diagnoser 核对期望值：测试期望写错是这类失败最常见的原因，越早判越省轮数。
            # Assertion-only failures: let the Diagnoser check the expectations first; a wrong expectation is the commonest cause and the earlier it is found the fewer rounds are wasted.
            only_assertions=bool(analysis['items']) and all(i['kind']=='assertion' for i in analysis['items'])
            wants=(repeats>=2 or (primary=='ambiguous' and history>=2) or (only_assertions and not self.job.get('diagnoses')))
            can=bool(self._ladder and self.router.policy.get('diagnose') and diagnoses<int(self.router.policy.get('diagnose_max',0)))
            result={'primary':primary,'format_only':format_only(checks,self.pack),'can_diagnose':can,
                    'arbitrate_due':bool(assertion) and self.job.get('repeated_assertions',0)==2 and not self.job.get('arbitrated'),
                    'needs_diagnosis':bool(can and wants),
                    'rewrite_due':bool(self._ladder and primary in ('implementation','ambiguous') and int(self.job.get('stall',0))>=REWRITE_AFTER_STALLS
                                       and int(self.job.get('rewrites',0))<MAX_REWRITES and self._strongest_tried())}
            return result

        def diagnose(ctx):
            try:
                diagnosis=self._diagnose(verified,analysis,checks,bundle)
            except RoutingStop:
                diagnosis=None  # 预算/等级不允许诊断：直接修复，不因此停下 / cannot afford it: just fix
            except Exception as exc:  # 诊断只是建议：任何失败都不能挡住修复，但要留下记录 / advisory: no failure may block the fix, but record it
                diagnosis=None
                self._emit('diagnosis_failed',{'error':str(exc)[:200]},verified,always=True)
            self.job['diagnoses']=int(self.job.get('diagnoses',0))+1
            return {'diagnosed':True,'diagnosis':diagnosis,'can_diagnose':False}

        def instructions(owner):
            parts=[ownership.hint(analysis,owner,self.pack),self._attempt_summary()]
            diagnosis=ctx_diagnosis()
            if diagnosis:
                key='implementation_instructions' if owner=='implementation' else 'test_instructions'
                if diagnosis.get(key):parts.append('【Diagnoser 的诊断】'+diagnosis['rationale']+'\n【Diagnoser 的指导】'+diagnosis[key])
            if self.job.get('referee_text'):parts.append(self.job['referee_text'])
            if self.job.get('revision_ineffective'):
                parts.append('The previous test revision did NOT change these failing assertions (they still fail with the same message): '+' | '.join(self.job['revision_ineffective'])
                             +'. Do not make a cosmetic edit again: recompute the expected value from the requirement and the test setup, or conclude that the implementation is wrong.')
            return '\n'.join(p for p in parts if p)

        def ctx_diagnosis():
            return facts.get('diagnosis')

        def run_fix(chain, role, stage, make, admit=None):
            used={}
            try:
                draft=self._attempt(chain,role,lambda provider,fb:self._track(used,provider,make(provider,fb)),
                                    need=admit or need,stage=stage,reject_noop=True)
            except NoChange:
                self._log_fix(stage,None,{})
                return {'noop':True,'halted':False,'noop_count':int(facts.get('noop_count',0))+1}
            except NoProgress as exc:
                return {'halted':True,'halt_kind':'noop','halt_reason':str(exc)}
            except ExpectationSuspected as exc:
                # 信号 S1 + 独立裁判：实际值一点没变、裁判多数认为测试期望错了 → 立刻转去修订测试，并把裁判的证据写进修订指令（不再攒 noop 次数）。
                # Signal S1 + the independent referee: the actual value did not move and the referee majority judged the expectation wrong -> revise the tests at once with the referee's evidence in the instructions (no waiting for noop counts).
                if chain=='fix:implementation' and not facts.get('flipped'):
                    self.job['referee_text']=exc.referee_text
                    self._emit('repair_wants_tests',{'reason':str(exc)[:200],'signal':'stable_actual_value'},verified,always=True)
                    return {'halted':True,'halt_kind':'noop','halt_reason':str(exc)}
                raise
            except MasaError as exc:
                # 修复实现的模型一再想改冻结的测试/go.mod/文件结构（被校验拒绝）：说明它认为问题在测试里。别让整个任务失败，
                # 按“最强模型不改实现”同样处理——转去修订测试一次（真实评测里 wc 因此失败过一次）。
                # A model asked to repair the implementation keeps trying to change frozen files (rejected by validation): it believes the problem is in the tests.
                # Do not fail the whole task; treat it like "the strongest model will not change the implementation" and revise the tests once (a real benchmark run failed this way).
                if chain=='fix:implementation' and 'repair cannot change tests' in str(exc) and not facts.get('flipped'):
                    self._emit('repair_wants_tests',{'reason':str(exc)[:200]},verified,always=True)
                    return {'halted':True,'halt_kind':'noop','halt_reason':str(exc)}
                raise
            if used:self.job['pending_fix']={**used,'chain':chain}
            self._log_fix(stage,draft,used)
            return {'draft':draft,'noop':False,'halted':False,'halt_kind':None}

        def format_files(ctx):
            phase('test_format',verified,attempt+1)
            return {'draft':generation.format_test_files(verified,lambda rid:phase('test_format',rid,attempt+1))}

        def fix_implementation(ctx):
            phase('repair',verified,attempt+1)
            return run_fix('fix:implementation','project_repair','repair',lambda provider,fb:generation.repair(
                verified,provider,'\n'.join(x for x in (instructions('implementation'),fb) if x),
                lambda rid:phase('repair',rid,attempt+1),use_intelligence=True),admit=generation.repair_need(bundle,evidence))

        def revise_tests(ctx):
            phase('test_revision',verified,attempt+1)
            base='Fix the recorded test-file errors, including any '+self.pack.ui().get('format_name','format')+' failure. Preserve behavioral assertions and requirements.'
            flipped=not facts.get('flipped') and (facts.get('halt_kind')=='noop' or int(facts.get('noop_count',0))>=int(facts.get('noop_limit',3)))
            if flipped:
                # 最强模型多次审视后认为实现无需改动：逐条重新手算期望值（字节数要把换行和多字节字符算进去），只改算错的期望。
                # The strongest model reviewed the implementation repeatedly and changed nothing: recompute EVERY expected value by hand and change only the wrong ones.
                base=('The implementation was reviewed several times by the strongest model and judged correct, yet these assertions still fail. '
                      'Suspect the test expectations: recompute each failing expected value by hand from the input and the requirement '
                      '(count newline characters and multi-byte characters in byte counts), fix only expectations that are wrong, '
                      'and never delete cases or weaken checks.')
            out=run_fix('fix:test','project_test_revision','test_revision',lambda provider,fb:generation.revise_tests(
                verified,provider,'\n'.join(x for x in (base,instructions('test'),fb) if x),
                lambda rid:phase('test_revision',rid,attempt+1)))
            return {**out,'flipped':True} if flipped else out

        def rewrite_implementation(ctx):
            # 整体重写：连续几轮补丁都没有改善，说明结构本身可能是错的。最强模型丢弃现有结构，对着冻结的测试重新写实现；
            # 给全部实现文件（不做相关性裁剪），因为重写可能要改动任何一个。
            # Whole rewrite: several patch rounds without improvement suggest the structure itself is wrong. The strongest model discards it and
            # writes the implementation again against the frozen tests, with every implementation file supplied (a rewrite may touch any of them).
            self.job.update(rewrites=int(self.job.get('rewrites',0))+1,stall=0)
            self._emit('rewrite_started',{'after_rounds':len(self.job.get('fix_log',[])),'unresolved':self.job.get('unresolved_now')},verified,always=True)
            phase('repair',verified,attempt+1)
            base=('REWRITE. Several patch rounds in a row did not reduce the failures, so the current structure is probably wrong. Do NOT patch it: '
                  'discard its design and write the implementation files again from scratch so that the frozen tests pass. Keep exactly the identifiers '
                  'and signatures the tests use. Choose the simplest design that satisfies the approved acceptance criteria (for example read all input at once '
                  'instead of keeping line-by-line state) and read the earlier rounds below to avoid repeating what failed.')
            out=run_fix('fix:implementation','project_repair','rewrite',lambda provider,fb:generation.repair(
                verified,provider,chr(10).join(x for x in (base,instructions('implementation'),fb) if x),
                lambda rid:phase('repair',rid,attempt+1),use_intelligence=False))
            return {**out,'rewritten':True}

        def arbitrate_tests(ctx):
            # 实现修复后同一断言仍失败：测试期望可能与已批准规格矛盾。让 Tester 以规格为准仲裁一次，
            # 此后仍相同则由三次规则停止并交给人。 Same assertion survives an implementation repair: let the Tester
            # arbitrate once against the approved spec; if it still repeats, the three-strike rule hands over to a human.
            self.job.update(arbitrated=True)
            phase('test_revision',verified,attempt+1)
            base=('The same assertions kept failing after an implementation repair. Compare EACH failing assertion with the approved '
                  'spec.acceptance and the goal text. If an expected value contradicts them, correct that expectation to follow the '
                  'requirement; do not delete cases or weaken checks. If the test already follows the requirement, change nothing '
                  'about that assertion.')
            return run_fix('fix:ambiguous','project_test_revision','arbitration',lambda provider,fb:generation.revise_tests(
                verified,provider,'\n'.join(x for x in (base,fb) if x),lambda rid:phase('test_revision',rid,attempt+1)))

        actions={'classify':classify,'diagnose':diagnose,'format_files':format_files,'fix_implementation':fix_implementation,
                 'revise_tests':revise_tests,'arbitrate_tests':arbitrate_tests,'rewrite_implementation':rewrite_implementation}
        engine=FlowEngine(self.job['request'].get('workflow') or FIX_V1,actions,max_steps=12,on_step=lambda step:self._trace_step(step,verified))
        return engine.run(facts)

    def _log_fix(self, stage, draft, used):
        """记录每次修复产出了什么（谁做的、改了哪些文件），Diagnoser 据此知道“已经试过什么”。
        Log what each fix did (who, which files) so the Diagnoser knows what was already tried."""
        changed=(self.store.run(draft)['data'].get('project_plan',{}).get('changed_files') or []) if draft else []
        log=list(self.job.get('fix_log',[]))[-5:]
        model=next((c.model for c in self.router.candidates if c.id==used.get('candidate')),None) if used else None
        log.append({'stage':stage,'changed':changed[:6],'level':used.get('level'),'candidate':used.get('candidate'),'model':model,
                    'before':self.job.get('unresolved_now')})
        self.job['fix_log']=log

    def _close_attempt(self, analysis):
        """新一轮验证出来之后，把上一次修复的结果补进日志（见 attempts.close_attempt）。 Record the previous fix's outcome (see attempts.close_attempt)."""
        attempts.close_attempt(self.job,analysis)

    def _attempt_summary(self):
        """前几轮“试过什么、结果如何”的摘要（见 attempts.summary）。 Summary of earlier rounds (see attempts.summary)."""
        return attempts.summary(self.job)

    def _extend_deadline(self, run_id):
        """用户明确要求继续时，已过期的验证 run 需要延长期限才能再做角色调用。 Extend an expired run's deadline for an explicit continuation."""
        run=self.store.run(run_id)
        if time.time()>=run['data']['deadline_at']-60:
            self.store.save_metadata(run_id,'deadline_at',time.time()+3600,'deadline_extended',payload={'reason':'continued by the user'})

    def _failure_context(self, verified, analysis, checks, bundle):
        """Diagnoser、测试怀疑者、指挥者共用的输入片段：任务目标、已批准的规格、失败涉及的文件、失败证据。
        Shared input of the Diagnoser, the Test Skeptic and the Conductor: goal, approved spec, the files the failures point at, failure evidence."""
        store=self.store
        run=store.run(verified)
        approved=store.read(store.read(run['data']['project_bundle']['approval_ref'])['spec_approval_ref'])
        files={}
        budget=20000
        wanted=[i['path'] for i in analysis['items'] if i['path']]
        for path in dict.fromkeys(wanted):
            # 断言条目里只有文件名（如 cli_test.go），要还原成 bundle 里的完整路径；之前没还原，测试源码从未送进 Diagnoser。
            # Assertion items carry only a file name (cli_test.go): resolve it to the full bundle path (previously the test source never reached the Diagnoser).
            keys=[k for k in bundle if k==path or k.endswith('/'+path)]
            full=keys[0] if len(keys)==1 else None
            if full and full not in files and budget>0:
                files[full]=bundle[full][:6000];budget-=len(files[full])
        evidence=[{'operation':op,'output':(str(r.get('stdout',''))+'\n'+str(r.get('stderr','')))[:3000]} for op,r in checks if r.get('exit_code')!=0][:3]
        return run,approved,files,evidence

    def _referee(self, verified, analysis, bundle, samples=3):
        """断言裁判（策略 assertion_referee，默认关闭）：对失败的断言，便宜的云端模型在打乱成 A/B 的两个值里独立选 3 次，多数作为独立证据交给 Diagnoser 和修复指令。
        每次调用都经过持久角色层（计入预算、可恢复）；任何失败都返回 None，流程照旧。
        Assertion referee (policy assertion_referee, off by default): for failing assertions a cheap cloud model makes three independent forced choices between the two values shuffled into A/B; the majority becomes
        independent evidence for the Diagnoser and the fix instructions. Every call goes through the durable role layer; any failure returns None and the flow goes on as before."""
        if not (self._ladder and self.router.policy.get('assertion_referee')):
            return None
        provider=self.router.cheapest('assertion_referee')
        if provider is None:
            return None
        cases=[]
        for item in analysis['items']:
            if item['kind']==ownership.ASSERTION and len(cases)<6:
                case=referee.case_for(len(cases),item,bundle,self.pack)
                if case:cases.append(case)
        if not cases:
            return None
        run=self.store.run(verified)
        approved=self.store.read(self.store.read(run['data']['project_bundle']['approval_ref'])['spec_approval_ref'])
        context={'goal':run['data']['goal'],'acceptance':approved['spec']['acceptance'],'cases':[referee.public(c) for c in cases]}
        drawn=[]
        for k in range(samples):
            try:
                answer=RoleRuntime(self.store).call(verified,provider,'assertion_referee',context,invocation_id='initial' if k==0 else f's{k}',freeze_key='referee_snapshot_ref')
                drawn.append({a['index']:a['choice'] for a in answer['answers']})
            except CallCancelled:
                raise
            except Exception:
                drawn.append(None)
        result=referee.tally(cases,drawn)
        self.store.event(verified,'assertion_referee',{'cases':len(cases),'verdicts':{str(i):r['verdict'] for i,r in result.items()},'samples':sum(1 for d in drawn if d is not None)})
        return referee.evidence_text(cases,result) or None

    def _diagnose(self, verified, analysis, checks, bundle):
        """Diagnoser：只读、用最高等级、判断“谁的问题”并给出具体指导。结果持久化在角色账本里。
        Read-only Diagnoser at the strongest level: decides whose problem it is and gives concrete instructions."""
        store=self.store
        run,approved,files,evidence=self._failure_context(verified,analysis,checks,bundle)
        context={'goal':run['data']['goal'],'acceptance':approved['spec']['acceptance'],'all_files':sorted(bundle),'files':files,
                 'ownership':ownership.describe(analysis),'failure_evidence':evidence,
                 'history':list(self.job.get('fix_log',[])),'noop_streak':int(self.job.get('noop_streak',0))}
        evidence_text=self._referee(verified,analysis,bundle)
        if evidence_text:
            context['independent_evidence']=evidence_text
            self.job['referee_text']=evidence_text
        provider,decision=self._pick('project_diagnoser','diagnose',need=context,anchor=verified,stage='diagnose')
        self._extend_deadline(verified)
        try:
            diagnosis=RoleRuntime(store).call(verified,provider,'project_diagnoser',context)
        except TransportFailure:
            if self._wait_for_server(provider,0):
                diagnosis=RoleRuntime(store).call(verified,provider,'project_diagnoser',context)
            else:raise
        from masa.domain.proposals import reconcile_diagnosis, validate_diagnosis
        diagnosis=validate_diagnosis(diagnosis)
        # 结论必须服从 Diagnoser 自己逐条核对的结果（它被测试带偏过：见 reconcile_diagnosis）。
        # The verdict must obey the Diagnoser's own per-case checks (it has been anchored by the test before: see reconcile_diagnosis).
        verdict=diagnosis['owner']
        diagnosis,changed=reconcile_diagnosis(diagnosis)
        self._emit('diagnosis',{'owner':diagnosis['owner'],'rationale':diagnosis['rationale'][:400],'by':(provider.profile or {}).get('model'),
                                'reconciled_from':verdict if changed else None,
                                'mismatches':[c['case'] for c in diagnosis['expectation_checks'] if not c['matches']][:6]},verified,always=True)
        return diagnosis

    def _seed_history_from_lineage(self, verified):
        """从版本链重建“修复链历史”：每个由模型产出、之后验证仍失败的修复，记为该等级的一次失败。
        Rebuild the fix-chain history from the version lineage: each model-made fix whose verification still failed counts as a failure."""
        if any(k.startswith('fix') for k in self.job.get('route_history',{})) or not self._ladder:
            return
        by_model={c.model:c for c in self.router.candidates}
        history=[]
        current=verified
        seen=set()
        runs={r['id']:r for r in self.store.all_runs()}
        while current in runs and current not in seen:
            seen.add(current)
            run=runs[current]
            parent=run['data'].get('parent_run_id')
            if run['data'].get('project_bundle') and parent in runs:
                draft=runs[parent]
                plan=draft['data'].get('project_plan') or {}
                if plan.get('repair_of') and not plan.get('format_only'):
                    asked=[e for e in self.store.events(parent) if e['type']=='model_requested']
                    model=(asked[-1]['payload'].get('route') or {}).get('model') if asked else None
                    chain='fix:test' if plan.get('revision_scope')=='tests' else 'fix:implementation'
                    if model in by_model:
                        history.append((chain,{'candidate':by_model[model].id,'level':by_model[model].level}))
            current=parent
        history.reverse()
        if history:
            all_history=dict(self.job.get('route_history',{}))
            for chain,entry in history:
                all_history[chain]=list(all_history.get(chain,[]))+[entry]
            self.job['route_history']=all_history
            self._emit('history_seeded',{'fix_failures':{c:[e['level'] for ch,e in history if ch==c] for c in {c for c,_ in history}}},verified,always=True)

    def _track(self, used, provider, value):
        """记录最近一次成功产出修复草稿的候选。 Remember which candidate produced the latest fix draft."""
        entry=self.router.entry_of(provider)
        if entry:used.update(candidate=entry['id'],level=entry['level'])
        return value
