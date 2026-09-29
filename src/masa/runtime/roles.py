"""持久角色调用：结果复用与不确定状态保护。 Durable role calls with conservative recovery."""
import time
from masa.domain.models import MasaError
from masa.infrastructure.locking import owner_lock


class RoleRuntime:
    def __init__(self, store):
        """复用运行账本，独立记录每个角色输入与结果。 Reuse the run ledger and persist role inputs/results."""
        self.store = store
        store.db.execute('''CREATE TABLE IF NOT EXISTS role_calls (
            run_id TEXT NOT NULL REFERENCES runs(id), purpose TEXT NOT NULL,
            input_ref TEXT NOT NULL, route_ref TEXT NOT NULL, status TEXT NOT NULL,
            output_ref TEXT, started REAL NOT NULL, finished REAL,
            PRIMARY KEY(run_id,purpose))''')
        store.db.commit()

    def call(self, rid, provider, purpose, values):
        """完成的调用不再付费重放；未知调用要求显式处理。 Reuse completed calls; never silently replay uncertain requests."""
        context = {'purpose': purpose, **values}
        context_ref = self.store.put(context)
        route_ref = self.store.put(provider.profile)
        with owner_lock(self.store.root/'role-locks'/f'{rid}.lock'):
            run = self.store.run(rid)
            if run['cancel_requested'] or time.time() >= run['data']['deadline_at']:
                raise MasaError('role run cancelled or deadline expired')
            row = self.store.db.execute('SELECT * FROM role_calls WHERE run_id=? AND purpose=?', (rid,purpose)).fetchone()
            if row:
                if row['input_ref'] != context_ref or row['route_ref'] != route_ref:
                    raise MasaError('role input or provider changed; create a new revision')
                if row['status'] == 'completed':
                    return self.store.read(row['output_ref'])
                raise MasaError('role call '+row['status']+': result unavailable; create an explicit retry revision')
            if any(e['type']=='model_requested' and e['payload'].get('step_id')==purpose for e in self.store.events(rid)):
                raise MasaError('legacy role request has no durable checkpoint; create an explicit revision')
            # 意图、预算和开始事件原子提交，崩溃不能绕过预算。
            # Commit intent and budget together before any network request.
            with self.store.transaction():
                if run['model_calls'] >= run['data']['budget']['model_calls']:
                    raise MasaError('model_call_budget_exhausted')
                self.store.db.execute('UPDATE runs SET model_calls=model_calls+1 WHERE id=?',(rid,))
                self.store.db.execute('INSERT INTO role_calls VALUES(?,?,?,?,?,?,?,?)',
                    (rid,purpose,context_ref,route_ref,'running',None,time.time(),None))
                self.store._event(rid,'model_requested',{'step_id':purpose,'route':provider.profile,'context_ref':context_ref})
            started=time.monotonic()
            try:
                output=provider.respond(context)
            except Exception:
                with self.store.transaction():
                    self.store.db.execute("UPDATE role_calls SET status='failed',finished=? WHERE run_id=? AND purpose=?",(time.time(),rid,purpose))
                    self.store._event(rid,'model_failed',{'step_id':purpose,'usage':getattr(provider,'usage',None)})
                raise
            output_ref=self.store.put(output)
            with self.store.transaction():
                self.store.db.execute("UPDATE role_calls SET status='completed',output_ref=?,finished=? WHERE run_id=? AND purpose=?",(output_ref,time.time(),rid,purpose))
                self.store._event(rid,'model_completed',{'step_id':purpose,'response_ref':output_ref,
                    'usage':getattr(provider,'usage',None),'duration_ms':round((time.monotonic()-started)*1000)})
            return output

    def states(self, rid):
        """读取真实持久角色状态。 Read persisted role states for inspection and recovery."""
        return [dict(r) for r in self.store.db.execute('SELECT * FROM role_calls WHERE run_id=? ORDER BY started',(rid,))]
