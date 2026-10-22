"""持久角色调用：结果复用与不确定状态保护。 Durable role calls with conservative recovery."""
import time
from masa.domain.models import MasaError, TransportFailure
from masa.infrastructure.locking import owner_lock
from masa.infrastructure.llm import ChatProvider
from masa.runtime import live, user_input


class RoleRuntime:
    def __init__(self, store):
        """复用运行账本，独立记录每个角色输入与结果。 Reuse the run ledger and persist role inputs/results."""
        self.store = store
        store.db.execute('''CREATE TABLE IF NOT EXISTS role_calls (
            run_id TEXT NOT NULL REFERENCES runs(id), purpose TEXT NOT NULL,
            input_ref TEXT NOT NULL, route_ref TEXT NOT NULL, status TEXT NOT NULL,
            output_ref TEXT, started REAL NOT NULL, finished REAL,
            PRIMARY KEY(run_id,purpose))''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS role_invocations (
            run_id TEXT NOT NULL REFERENCES runs(id), purpose TEXT NOT NULL,
            invocation_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
            input_ref TEXT NOT NULL, route_ref TEXT NOT NULL, status TEXT NOT NULL,
            output_ref TEXT, started REAL NOT NULL, finished REAL,
            PRIMARY KEY(run_id,purpose,invocation_id), UNIQUE(run_id,purpose,attempt_no))''')
        store.db.commit()
        store.db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)')
        store.db.commit()
        # 同一事务记录迁移完成；后续初始化不再扫描旧调用表。
        # Record migration atomically; future initializations never rescan legacy calls.
        with store.transaction():
            if not store.db.execute("SELECT 1 FROM schema_migrations WHERE name='role_invocations_v1'").fetchone():
                store.db.execute("""INSERT OR IGNORE INTO role_invocations
                    SELECT run_id,purpose,'initial',1,input_ref,route_ref,status,output_ref,started,finished FROM role_calls""")
                store.db.execute("INSERT INTO schema_migrations VALUES('role_invocations_v1')")

    def call(self, rid, provider, purpose, values, *, invocation_id="initial", freeze_key="model_snapshot_ref", continue_after_failure=False):
        """完成的调用不再付费重放；未知调用要求显式处理。 Reuse completed calls; never silently replay uncertain requests.
        freeze_key：同一个 run 里由不同模型承担的“职位”（如更强的测试作者）各自冻结自己的配置，互不冲突。
        continue_after_failure：上一次调用明确失败（已有结论，不是结果不明）时允许继续下一次调用——自主循环里一步写坏了不该让整个循环作废。
        continue_after_failure: allow the next call after a previous call that definitely failed (a known outcome, not an uncertain one): in an autonomous loop one bad step must not void the loop.
        freeze_key: a distinct role slot in one run (e.g. a stronger test author) freezes its own configuration without clashing."""
        if not isinstance(invocation_id,str) or not invocation_id or len(invocation_id)>100:
            raise MasaError('invalid role invocation id')
        if 'purpose' in values:
            raise MasaError('role values cannot override purpose')
        context = {'purpose': purpose, **values}
        # 非默认语言的运行把语言写进每次角色调用的上下文：提示词与校验据此选语言包；默认语言的上下文保持原样（逐字节相同）。
        # A run in a non-default language puts it into every role context so prompts and validators pick the pack; default-language contexts stay byte-identical.
        language = self.store.run(rid)['data'].get('language')
        if language and 'language' not in values:
            context['language'] = language
        # 用户在任务运行中追加的提示：只进入“之后才开始”的调用；已存在的调用沿用它当时的提示（否则输入身份对不上）。
        # User notes added while the task runs: only calls that start later get them; an existing invocation keeps the notes it was created with (or its input identity would change).
        if 'user_notes' not in values:
            existing = self.store.db.execute('SELECT input_ref FROM role_invocations WHERE run_id=? AND purpose=? AND invocation_id=?', (rid, purpose, invocation_id)).fetchone()
            notes = self.store.read(existing['input_ref']).get('user_notes') if existing else user_input.notes_for(self.store, rid)
            if notes:
                context['user_notes'] = notes
        context_ref = self.store.put(context)
        route_ref = self.store.put(provider.profile)
        with owner_lock(self.store.root/'role-locks'/f'{rid}.lock'):
            run = self.store.run(rid)
            if run['cancel_requested'] or run['status']=='cancelled' or time.time() >= run['data']['deadline_at']:
                raise MasaError('role run cancelled or deadline expired')
            retry_same = paid_retry = False
            row = self.store.db.execute('SELECT * FROM role_invocations WHERE run_id=? AND purpose=? AND invocation_id=?', (rid,purpose,invocation_id)).fetchone()
            if row:
                if row['input_ref'] != context_ref or row['route_ref'] != route_ref:
                    raise MasaError('role input or provider changed; create a new revision')
                if row['status'] == 'completed':
                    return self.store.read(row['output_ref'])
                # 免费本地调用：请求遗留（进程崩溃）或没有收到响应（服务被杀）时，可以作为同一次调用的新尝试重试；
                # 付费云调用与已收到响应的失败仍然拒绝。 Free local calls may be re-attempted; paid calls and received failures may not.
                if not self._free_retry(rid, purpose, invocation_id, row, provider):
                    # 不确定的付费调用：请求可能已被远端受理而本地没收到结果，进程退出前后的真实状态无法凭空确定。默认暂停（待处理）；只有显式允许才发起新的一次付费尝试：
                    # 策略 unknown_paid_calls = retry，或有人在待处理队列里对这一次调用选了 retry（账本里有 uncertain_call_resolved 事件）。
                    # An uncertain paid call: the provider may have accepted the request while no result reached us, and the real state cannot be known. By default it pauses (pending); a new paid attempt is made only when explicitly allowed:
                    # policy unknown_paid_calls = retry, or someone chose retry for this very call in the pending queue (an uncertain_call_resolved event in the ledger).
                    if not self._paid_retry(rid, purpose, invocation_id, row, provider):
                        raise MasaError('role call '+row['status']+': result unavailable; resolve it in the pending queue (retry) or create an explicit retry revision')
                    paid_retry = True
                retry_same = True
            previous=self.store.db.execute('SELECT * FROM role_invocations WHERE run_id=? AND purpose=? ORDER BY attempt_no DESC LIMIT 1',(rid,purpose)).fetchone()
            if not retry_same:
                if previous and previous['status']!='completed' and not (continue_after_failure and previous['status']=='failed'):
                    raise MasaError('previous role result unavailable; cannot bypass unresolved invocation')
                if invocation_id!='initial' and not previous:
                    raise MasaError('role continuation requires an initial invocation')
            attempt_no=previous['attempt_no']+1 if previous else 1
            if not previous and any(e['type']=='model_requested'  and e['payload'].get('step_id')==purpose for e in self.store.events(rid)):
                raise MasaError('legacy role request has no durable checkpoint; create an explicit revision')
            # 首次角色调用记录无密钥配置，沿用现有 artifact 与事务。
            # Persist secret-free configuration using existing artifacts and transaction boundaries.
            snapshot=getattr(provider,'snapshot',None)
            snapshot_ref=self.store.put(snapshot) if snapshot is not None else None
            frozen=run['data'].get(freeze_key)
            if frozen and frozen!=snapshot_ref:
                raise MasaError('task model configuration changed; restore its snapshot')
            # 意图、预算和开始事件原子提交，崩溃不能绕过预算。
            # Commit intent and budget together before any network request.
            with self.store.transaction():
                if retry_same:
                    # 同一次逻辑调用的新尝试：不再占用调用次数预算，旧尝试在事件里标记为被放弃。
                    # A new attempt of the same logical call: no extra budget; the old attempt is marked abandoned in the events.
                    if paid_retry:
                        # 付费的新尝试是一次真实的第二次请求：占用调用次数预算。 A paid re-attempt is a real second request: it uses the call budget.
                        if run['model_calls'] >= run['data']['budget']['model_calls']:
                            raise MasaError('model_call_budget_exhausted')
                        self.store.db.execute('UPDATE runs SET model_calls=model_calls+1 WHERE id=?',(rid,))
                    if row['status'] == 'running' or paid_retry:
                        self.store._event(rid,'model_abandoned',{'step_id':purpose,'invocation_id':invocation_id,'attempt_no':row['attempt_no'],
                                                                 'reason':'explicit retry of an uncertain paid call' if paid_retry else 'process died or request lost; local call retried'})
                    self.store.db.execute("UPDATE role_invocations SET attempt_no=?,status='running',output_ref=NULL,started=?,finished=NULL WHERE run_id=? AND purpose=? AND invocation_id=?",
                                          (attempt_no,time.time(),rid,purpose,invocation_id))
                else:
                    if run['model_calls'] >= run['data']['budget']['model_calls']:
                        raise MasaError('model_call_budget_exhausted')
                    if snapshot_ref and not frozen:
                        self.store.save_metadata(rid,freeze_key,snapshot_ref,'model_configuration_frozen',
                                                 payload={'snapshot_ref':snapshot_ref,'mode':'fixed'})
                    self.store.db.execute('UPDATE runs SET model_calls=model_calls+1 WHERE id=?',(rid,))
                    self.store.db.execute('INSERT INTO role_invocations VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (rid,purpose,invocation_id,attempt_no,context_ref,route_ref,'running',None,time.time(),None))
                self.store._event(rid,'model_requested',{'step_id':purpose,'invocation_id':invocation_id,'attempt_no':attempt_no,'route':provider.profile,'context_ref':context_ref})
            started=time.monotonic()
            try:
                # 真实网络调用不能超过任务剩余时间；旧测试提供商仍使用原签名。
                # Bound real requests by the remaining run deadline without changing fake providers.
                if isinstance(provider,ChatProvider):
                    # 流式：每个数据块之间检查取消标志，并上报实时进度。 Streaming: check the cancel flag between chunks and report live progress.
                    provider.should_cancel=lambda:bool(self.store.run(rid)['cancel_requested'])
                    live.begin(rid,purpose,invocation_id,(provider.profile or {}).get('model'))
                    provider.on_progress=lambda info:live.update(rid,purpose,(provider.profile or {}).get('model'),info)
                    provider.on_text=lambda text:live.append(rid,text)
                    remaining=run['data']['deadline_at']-time.time()
                    if remaining<=0:
                        raise MasaError('role run cancelled or deadline expired')
                    output=provider.respond(context,timeout=min(provider.config['timeout_seconds'],remaining))
                else:
                    output=provider.respond(context)
            except Exception as failure:
                live.clear(rid)
                with self.store.transaction():
                    self.store.db.execute("UPDATE role_invocations SET status='failed',finished=? WHERE run_id=? AND purpose=? AND invocation_id=?",(time.time(),rid,purpose,invocation_id))
                    self.store._event(rid,'model_failed',{'step_id':purpose,'invocation_id':invocation_id,'attempt_no':attempt_no,'usage':getattr(provider,'usage',None),'metrics':getattr(provider,'metrics',None),
                        'contract_diagnostic':getattr(provider,'contract_diagnostic',None),
                        # 没有收到响应：免费本地调用可以安全重试。 No response received: safe to retry for free local calls.
                        'transport':isinstance(failure,TransportFailure)})
                raise
            live.clear(rid)
            output_ref=self.store.put(output)
            with self.store.transaction():
                self.store.db.execute("UPDATE role_invocations SET status='completed',output_ref=?,finished=? WHERE run_id=? AND purpose=? AND invocation_id=?",(output_ref,time.time(),rid,purpose,invocation_id))
                self.store._event(rid,'model_completed',{'step_id':purpose,'invocation_id':invocation_id,'attempt_no':attempt_no,'response_ref':output_ref,
                    'usage':getattr(provider,'usage',None),'metrics':getattr(provider,'metrics',None),'duration_ms':round((time.monotonic()-started)*1000)})
            # 收到的完整输出先保留为证据；取消或过期后不再将其应用到业务流程。
            # Preserve a received response as evidence, but never apply it after cancellation/expiry.
            latest=self.store.run(rid)
            if latest['cancel_requested'] or latest['status']=='cancelled' or time.time()>=latest['data']['deadline_at']:
                raise MasaError('role run cancelled or deadline expired; completed response preserved')
            return output

    def _unanswered(self, rid, purpose, invocation_id, row):
        """这条调用确实没有拿到响应：进程崩溃遗留的 running，或最后一次失败是传输失败。已收到响应但被拒绝的失败不在此列（那是已知结果）。
        The call really got no response: a running row left by a crash, or a last failure that was a transport failure. A failure after a received response is a known outcome and does not count."""
        if row['status'] == 'running':
            return True
        if row['status'] != 'failed':
            return False
        last = [e for e in self.store.events(rid) if e['type'] == 'model_failed'
                and e['payload'].get('step_id') == purpose and e['payload'].get('invocation_id') == invocation_id]
        return bool(last) and last[-1]['payload'].get('transport') is True

    def _paid_retry(self, rid, purpose, invocation_id, row, provider):
        """付费调用能否作为新尝试重发：没拿到响应，并且被明确允许——策略 unknown_paid_calls = retry，或这一次调用有尚未用掉的 retry 决定。
        May a paid call be sent again: it got no response AND it is explicitly allowed - policy unknown_paid_calls = retry, or an unused retry decision exists for this very call."""
        if not self._unanswered(rid, purpose, invocation_id, row):
            return False
        if getattr(provider, 'retry_unknown_paid', False):
            return True
        events = self.store.events(rid)
        granted = sum(1 for e in events if e['type'] == 'uncertain_call_resolved' and e['payload'].get('action') == 'retry'
                      and e['payload'].get('step_id') == purpose and e['payload'].get('invocation_id') == invocation_id)
        used = sum(1 for e in events if e['type'] == 'model_abandoned' and e['payload'].get('step_id') == purpose and e['payload'].get('invocation_id') == invocation_id
                   and str(e['payload'].get('reason', '')).startswith('explicit retry'))
        return granted > used

    def _free_retry(self, rid, purpose, invocation_id, row, provider):
        """这条未完成的调用能否作为新尝试自动重试：必须是免费的本地模型、策略允许，并且调用确实没有拿到响应
        （进程崩溃遗留的 running，或最后一次失败是传输失败）。已收到响应但被拒绝的失败不在此列。
        May an unfinished call be re-attempted? Only for a free local model, when the policy allows it and no response was ever received."""
        if not getattr(provider, 'retry_unknown_calls', False) or (getattr(provider, 'config', None) or {}).get('model_type') != 'local':
            return False
        if row['status'] == 'running':
            return True
        if row['status'] != 'failed':
            return False
        last = [e for e in self.store.events(rid) if e['type'] == 'model_failed'
                and e['payload'].get('step_id') == purpose and e['payload'].get('invocation_id') == invocation_id]
        return bool(last) and last[-1]['payload'].get('transport') is True

    def states(self, rid):
        """读取真实持久角色状态。 Read persisted role states for inspection and recovery."""
        return [dict(r) for r in self.store.db.execute('SELECT * FROM role_invocations WHERE run_id=? ORDER BY started',(rid,))]
