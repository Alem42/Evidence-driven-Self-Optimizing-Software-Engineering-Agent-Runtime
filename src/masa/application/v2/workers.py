"""Runtime V2 的包工人循环：每个包一个自主循环——写文件、跑真实检查、读错误、修改，直到本包通过，再封包。
Runtime V2's package worker loop: one autonomous loop per package - write files, run the real checks, read the errors, fix, until the package passes and is sealed.

设计见 docs/design/RUNTIME_V2.md。这一版是“第一个可运行的切片”：
- 规划仍复用 V1 的 Planner/Tester（规格 + 示例），工人只替换“生成”这一段；生成出的草稿仍走 V1 的验证、Gate 与修复，所以 V2 的结果和 V1 用同一把尺子衡量。
- 工具集是语言包操作的包装，没有任意 shell；写范围由工具强制（只能写本包计划里的文件）。
- 每一步模型调用都经过持久角色层（exactly-once），崩溃后续跑只会重放已记录的步骤，不重复付费。
Design: docs/design/RUNTIME_V2.md. This is the first runnable slice: planning still reuses V1's Planner/Tester (spec + examples) and the workers replace only generation; the resulting draft still goes through V1's
verification, Gate and repair, so V2 is measured with the same yardstick. The tools wrap language-pack operations (no arbitrary shell), the write scope is enforced by the tools (only this package's planned files), and every
model step goes through the durable role layer (exactly-once), so a resumed run replays recorded steps instead of paying again.
"""
import json
import re
import shutil
import time
from pathlib import Path

from masa.application.checks import referee
from masa.application.generation import ProjectGeneration
from masa.application.v2 import memory
from masa.domain.models import MasaError, TransportFailure
from masa.application.orchestration.routing import CONTEXT_OUTPUT_RESERVE
from masa.domain.tokens import estimate_tokens
from masa.infrastructure import windows
from masa.roles import registry
from masa.runtime.roles import RoleRuntime
from masa.application.v2.workspace import Defer, Stuck, WorkerWorkspace, signature, unmet_dependencies, MAX_RESULT_CHARS, MAX_FILE_BYTES, FULL_INTERFACE_CHARS, MAX_ARBITRATIONS  # noqa: F401 (re-exported)

MAX_STEPS = 20  # 每个包最多的模型步数（硬上限，写进预算）/ hard per-package step limit (also written into the budget)
SOLO_FILES = 6  # 计划文件不多于这个数就合成一个包：小任务的测试常要启动整个程序，拆开会让“先写的包”永远等不到入口 / at most this many planned files make ONE package: small tasks' tests often start the whole program, and splitting makes the first package wait forever for the entry
STUCK_REPEATS = 3  # 同一个失败签名连续出现这么多次就判定卡住 / the same failure signature this many checks in a row means stuck
STUCK_REFUSALS = 4  # 连续这么多个被拒绝/无意义的动作也判定卡住 / this many refused or pointless actions in a row also means stuck
MAX_ROUNDS = 2  # 没有依赖信息时一个包最多被排进队列几次；实际上限随包的个数增长（见 _max_rounds）/ how many times a package may be scheduled; the real limit grows with the number of packages (see _max_rounds)
MAX_REPAIR_STEPS = 12  # 一次修复里工人的最多步数 / worker steps in one repair
INTEGRATORS = frozenset({'app', 'main', 'cli', 'cmd', 'runner', 'driver', 'dispatcher', 'controller', 'service'})  # 名字表明它把别的包接起来：依赖所有其它包 / the name says it wires other packages together: it depends on all the others
RECOVERABLE = ('invalid_json', 'incomplete')  # 已收到但不可用的回答：不是传输故障，当作被拒绝的一步让模型重来 / received but unusable answers: not a transport failure, treated as a refused step so the model tries again
SCRATCH_PATH = re.compile(r'[A-Za-z]:[\\/][^"\n]*?[\\/]workspaces[\\/][0-9a-f]+[\\/]')  # 验证用的工作区绝对路径 / the absolute path of a verification workspace
WORKER_CONTEXT_CHARS = 8000  # 工人上下文里除目标、验收、示例以外的部分的估计上限（本包文件、签名、最近结果、语言说明）/ estimated upper bound of a worker context beyond goal, acceptance and examples (the package's files, signatures, recent results, language notes)
TRANSPORT_GIVE_UP = 3  # 连续这么多步服务都没有回答就放弃 / give up after this many steps in a row without an answer
TRANSPORT_PAUSE = 3  # 第 n 次没有回答后等 n×这么多秒 / after the nth silent step wait n times this many seconds





def dependency_edges(pack, spec, groups):
    """{包: {它依赖的包}}：规格里每个文件的用途说明会提到它用到的别的包（例如“解析 stdin 并调用 parser 包”）；按包名/文件名在用途里出现来推断依赖。这是启发式，不是证明：
    推错了没有关系——工人在真正缺依赖时会被“推迟”（见 Defer），顺序只是让这种情况更少发生。
    {package: {packages it depends on}}: each planned file's purpose mentions the packages it uses; a package name or file stem appearing in another package's purposes is taken as a dependency. A heuristic, not a proof:
    a wrong guess is harmless because a worker that really lacks a dependency is deferred (see Defer); the order only makes that rarer."""
    module = str(spec.get('module', ''))
    names = {}
    for directory, items in groups:
        tokens = {directory.rsplit('/', 1)[-1].lower()} if directory else set()
        for item in items:
            stem = item['path'].rsplit('/', 1)[-1].rsplit('.', 1)[0].lower()
            if stem and not stem.startswith('__') and not stem.startswith('test_') and not stem.endswith('_test'):
                tokens.add(stem)
        names[directory] = {t for t in tokens if len(t) >= 3 and t != module.lower()}
    edges = {directory: set() for directory, _ in groups}
    entry_dir = pack.package_of(pack.entrypoint(spec), spec)
    for directory, items in groups:
        text = ' '.join(i['purpose'] for i in items).lower()
        for other, tokens in names.items():
            if other != directory and any(re.search(r'(?<![a-z0-9_])' + re.escape(t) + r'(?![a-z0-9_])', text) for t in tokens):
                edges[directory].add(other)
        # 名字是 app/main/cli…的包是“接线员”：规格用中文写用途时按名字匹配不到依赖（真实运行里 app 被排在第一个，写出了对不存在的包的猜测），所以按名字补上。
        # A package named app/main/cli… is the wiring package: purposes written in Chinese cannot be matched by package name (in a real run app was scheduled first and guessed at packages that did not exist), so add the edges by its name.
        if directory and directory != entry_dir and directory.rsplit('/', 1)[-1].lower() in INTEGRATORS:
            edges[directory] |= {d for d, _ in groups if d != directory and d != entry_dir}
    return edges


def order_packages(pack, spec, groups):
    """叶子先写：按依赖做拓扑排序，同级保持规格顺序，入口所在的包最后；有环就退回规格顺序。 Leaves first: topological order, ties keep the spec order, the entry's package last; a cycle falls back to the spec order."""
    entry_dir = pack.package_of(pack.entrypoint(spec), spec)
    edges = dependency_edges(pack, spec, groups)
    remaining = [d for d, _ in groups if d != entry_dir]
    order = []
    while remaining:
        ready = [d for d in remaining if not (edges[d] & set(remaining))]
        if not ready:  # 有环：不猜，按规格顺序 / a cycle: do not guess, keep the spec order
            order += remaining
            break
        order.append(ready[0])
        remaining.remove(ready[0])
    if any(d == entry_dir for d, _ in groups):
        order.append(entry_dir)
    by = dict(groups)
    return [(d, by[d]) for d in order], edges




class V2Generation(ProjectGeneration):
    """用包工人循环生成草稿；其余（规范化、整包校验、批准、验证、修复）沿用 V1。 Generates the draft with package worker loops; everything else (normalisation, bundle validation, approval, verification, repair) is V1's."""
    workers = True

    seed_run = None  # 上一次尝试的生成 run：已封包的包直接沿用，卡住的包带着它写了一半的文件继续 / the previous attempt's generation run: sealed packages are reused, a stuck package continues from its partial files

    def _packages(self, spec, planned):
        """按依赖顺序分组：[(目录, [计划文件项])]，叶子先写、入口所在的包最后；小任务合成一个包。 Group by dependency order: leaves first, the entry's package last; a small task is one package."""
        entry = self.pack.entrypoint(spec)
        if len(planned) <= SOLO_FILES:
            return [(self.pack.package_of(entry, spec), planned)] if planned else []
        groups = {}
        for item in planned:
            groups.setdefault(self.pack.package_of(item['path'], spec), []).append(item)
        ordered, edges = order_packages(self.pack, spec, list(groups.items()))
        self._edges = edges
        return ordered

    def _call_budget(self, approved, local_files):
        planned = [f for f in approved['spec']['files'] if f['path'] not in self.pack.seed_files(approved['spec'])]
        return max(8, len({self.pack.package_of(f['path'], approved['spec']) for f in planned}) * (MAX_STEPS + 1))

    def _examples(self, approved):
        test_op = self.pack.op_id('test')
        for check in approved.get('checks', []):
            if check.get('operation') == test_op:
                return check.get('cases', [])
        return []

    def _reusable(self, spec, files):
        """上一次尝试里已经封包的包（以及那次留下的所有文件，含卡住的包写了一半的文件）。 Packages sealed in the previous attempt, plus every file it left (including a stuck package's half-written files)."""
        if not self.seed_run:
            return set(), {}
        try:
            previous = self.store.run(self.seed_run)['data'].get('project_plan', {})
            kept = self.store.read(previous['partial_files_ref']) if previous.get('partial_files_ref') else {}
            sealed = {e['payload']['package'] for e in self.store.events(self.seed_run) if e['type'] in ('package_sealed', 'package_reused')}  # 上一次沿用的包也算：否则第三次尝试要把它们重写一遍 / what the previous attempt itself reused counts too, otherwise a third attempt rewrites them
        except (MasaError, KeyError):
            return set(), {}
        return sealed, {p: t for p, t in kept.items() if p in {f['path'] for f in spec['files']} and p not in files}

    def admission_need(self, goal, approved, growth):
        """工人每一步的上下文是 目标 + 验收 + 示例 + 本包文件 + 其它包的签名 + 最近结果（实测平均约 12 KB、最大约 17 KB）：按这个而不是整个规格估计，本地小模型才有机会被选中。
        A worker step's context is goal + acceptance + examples + the package's files + other packages' signatures + recent results (about 12 KB on average and 17 KB at most in real runs): estimate from that, not from the whole spec, so a small local model has a chance to be chosen."""
        return [goal, approved['spec']['acceptance'], self._examples(approved), 'x' * WORKER_CONTEXT_CHARS]

    def repair_need(self, bundle, evidence):
        """修复工人的上下文：失败证据 + 范围内的实现文件 + 其它包的签名，而不是整套代码。 A repair worker's context: the failure evidence, the in-scope implementation files and other packages' signatures, not the whole bundle."""
        return [evidence[:4000], 'x' * (WORKER_CONTEXT_CHARS + 4000)]

    referee_provider = None  # 断言裁判用的便宜云端模型（协调器设置；None = 没有裁判）/ the cheap cloud model for the assertion referee (set by the coordinator; None = no referee)
    _arbitrations = 0

    def _arbitrate(self, workspace, results):
        """断言裁判：把失败断言里真正不同的两个值打乱成 A/B，便宜的模型独立选 3 次，多数当作证据（实测单次 80%、三次多数 88%，docs/reference/TEST_RELIABILITY.md）。任何失败都返回空串。
        Assertion referee: the two values that really differ in the failing assertions are shuffled into A/B and a cheap model chooses three times independently; the majority is evidence (measured 80% single, 88% majority of three).
        Any failure returns an empty string."""
        provider = self.referee_provider
        if provider is None:
            return ''
        try:
            cases = []
            for diff in self.pack.assertion_diffs(results)[:4]:
                source = self.pack.test_function_source(workspace.files, diff['path'], diff['line'])
                cases.append(referee.case_from_values(len(cases), f"{diff['path']}:{diff['line']}", source, diff['got'], diff['want']))
            if not cases:
                return ''
            self._arbitrations += 1
            context = {'goal': workspace.goal, 'acceptance': workspace.spec['acceptance'], 'cases': [referee.public(c) for c in cases]}
            drawn = []
            for k in range(3):
                try:
                    answer = RoleRuntime(self.store).call(workspace.rid, provider, 'assertion_referee', context, invocation_id='initial' if workspace.rid not in self.__dict__.setdefault('_referee_runs', set()) else f'a{self._arbitrations}.{k}', freeze_key='referee_snapshot_ref')
                    self._referee_runs.add(workspace.rid)  # 一个 run 里该角色的第一次调用必须叫 initial / the first call of a role in a run must be called initial
                    drawn.append({a['index']: a['choice'] for a in answer['answers']})
                except TransportFailure:
                    drawn.append(None)
                except MasaError:
                    drawn.append(None)
            result = referee.tally(cases, drawn)
            workspace.last_referee = result  # 逐条结论：修复时据此决定是否转去修订测试 / the per-case verdicts: a repair uses them to decide whether to revise the tests instead
            self.store.event(workspace.rid, 'assertion_referee', {'cases': len(cases), 'verdicts': {str(i): r['verdict'] for i, r in result.items()}, 'samples': sum(1 for d in drawn if d is not None), 'package': workspace.directory})
            text = referee.evidence_text(cases, result)
            if text:
                text += chr(10) + 'Act on it: where the votes say the TEST is wrong, correct that expectation in your test file (recompute it from the requirement); where they say the CODE is wrong, change the code.'
            return text
        except Exception as exc:  # 裁判只是建议：任何失败都不能挡住工人，但要留下记录 / advisory: no failure may block the worker, but it is recorded
            self.store.event(workspace.rid, 'assertion_referee_failed', {'error': str(exc)[:200]})
            return ''

    def _max_rounds(self, total):
        """一个包最多被排进队列几次：每个包至多让路一次给它依赖的包，再加第一次。 How often a package may be scheduled: it may give way once per package, plus its first turn."""
        return max(MAX_ROUNDS, total + 1)

    def _context(self, workspace, goal, spec, approved, directory, purposes, results, step, max_steps, extra=None, strategy=None):
        context = {'goal': goal, 'acceptance': spec['acceptance'], 'examples': self._examples(approved), 'package': directory or '.',
                   'own_files': [{'path': p, 'purpose': purposes.get(p, '')} for p in sorted(purposes)], 'files': workspace.own_view(),
                   'interfaces': workspace.interfaces(), 'notes': workspace.notes,
                   'step': step, 'max_steps': max_steps, 'language_notes': self.pack.worker_notes(),
                   }
        context.update(memory.view(strategy or workspace.memory or memory.STRATEGIES['window'], results, workspace))  # 最近几步原文 + （策略需要时）摘要行/钉住项 / the recent steps verbatim plus (when the strategy asks) digest lines and pins
        context.update(self.pack.worker_context(spec))  # 语言包给的额外字段（例如模块路径与内部包导入路径） / extra fields from the language pack (e.g. module path and internal import paths)
        context.update(extra or {})
        allowed = workspace.allowed_tools()
        if len(allowed) < 6:
            context['allowed_tools'] = allowed
        return context

    def _begin_memory(self, rid, provider, workspace, directory):
        """这个循环用哪种记忆策略：先取得当前模型的有效上下文窗口（配置值、模型声明值、运行时分配值取小），再按预算选一档。window（默认）不查询任何东西。
        Which memory strategy this loop uses: first the current model's effective context window (configured, declared and runtime values, smallest wins), then a tier from the budget. `window` (the default) queries nothing."""
        name = getattr(self, 'memory_strategy', 'window') or 'window'
        if name == 'window':
            workspace.memory = memory.STRATEGIES['window']
            return None, None
        window = windows.resolve(getattr(provider, 'config', None) or {})
        spec = registry.get('package_worker')
        reserve = min(spec.max_output_tokens or CONTEXT_OUTPUT_RESERVE, CONTEXT_OUTPUT_RESERVE)
        budget = memory.input_budget(window['effective'], reserve, estimate_tokens(spec.prompt or ''))
        strategy = memory.start_strategy(name, budget)
        workspace.memory = strategy
        self.store.event(rid, 'memory_strategy', {'package': directory, 'strategy': strategy.name, 'requested': name, 'input_budget': budget, 'window': window})
        return budget, window

    def _worker_steps(self, rid, provider, roles, workspace, goal, spec, approved, directory, purposes, *, max_steps, round_no, prefix, retry_feedback=None, extra=None, defer=None, seed_results=None):
        """一个工人的循环：返回 (封包了吗, 步骤记录)；被推迟时抛出 Defer 的处理交给 defer 回调，返回 ('deferred', …)；卡住抛 Stuck。
        One worker's loop: returns (outcome, results) where outcome is 'sealed', 'deferred' or 'open' (the steps ran out); a stuck worker raises Stuck."""
        results, refused, silent = list(seed_results or []), 0, 0
        workspace.history = results
        budget, window = self._begin_memory(rid, provider, workspace, directory)
        for step in range(1, max_steps + 1):
            gate = getattr(self, 'pause_gate', None)
            if gate is not None:
                gate()  # 用户暂停：停在这一步之前 / the user paused: hold before this step

            def build(strategy, step=step):
                workspace.memory = strategy
                return self._context(workspace, goal, spec, approved, directory, purposes, results, step, max_steps, extra, strategy=strategy)
            # 单步输入必须放进模型的有效窗口：策略按预算从宽到紧，最紧仍放不下才抛 ContextOverflow（路由器换候选，不会被本地服务静默截断）。
            # One step's input must fit the model's effective window: the strategy steps from loose to tight, and only when even the tightest does not fit ContextOverflow is raised (the router picks another candidate; a local server never truncates silently).
            workspace.memory, context = memory.fit(workspace.memory, budget, build, model=(getattr(provider, 'config', None) or {}).get('model', ''), limit=(window or {}).get('effective') or 0)
            if retry_feedback and step == 1 and round_no == 1:
                context['previous_attempt_error'] = str(retry_feedback)[:1000]
            invocation = 'initial' if self._initial else f'{prefix}{round_no}.{step}:{directory}'[:100]  # 步号在前、可能很长的目录名在后：截断不会把不同的步合并（真实运行里 repair 的长目录名让所有步共用一个 id）/ round and step first, the possibly long directory last: truncation can never merge steps (in a real run a repair's long directory name made every step share one id)
            self._initial = False
            try:
                action = roles.call(rid, provider, 'package_worker', context, invocation_id=invocation, continue_after_failure=True)
            except TransportFailure as exc:
                # 服务没有回答（网络抖动）：一步只值几分钱，让整个任务因为一次抖动失败（真实运行里 28 次调用中的一次就让 bank 失败）比偶尔多付一步更贵。
                # 把它当作这一步失败，稍等再试下一步；连续失败 3 次才放弃。这是对“付费云调用不自动重放”的有意例外，只用于单步很小的工人循环。
                # The service did not answer (a network blip): a step costs a few fen, and failing the whole task over one blip (one of 28 calls failed a real bank run) costs more than occasionally paying for a step twice.
                # Treat it as a failed step, wait a moment and try the next one; give up only after 3 in a row. A deliberate exception to "paid cloud calls are never replayed", limited to worker loops whose steps are tiny.
                silent += 1
                self.store.event(rid, 'worker_step', {'package': directory, 'step': step, 'tool': None, 'path': None, 'ok': False, 'keys': [], 'summary': 'the model service did not answer: ' + str(exc)[:160], 'transport_failure': True})
                if silent >= TRANSPORT_GIVE_UP:
                    raise
                time.sleep(TRANSPORT_PAUSE * silent)
                continue
            except MasaError as exc:
                # 模型回答了但不能用（JSON 写坏、被截断）：不是传输故障，也不值得让整个生成阶段失败重来——当作一个被拒绝的步骤，带着原因让模型再来一次（真实运行里一个多余的 `,"}` 就让 7 步的工作作废）。
                # The model answered but the answer is unusable (broken JSON, cut off): not a transport failure and not worth failing and restarting the whole generation stage - treat it as a refused step and let the model try again with the reason
                # (in a real run one stray `,"}` voided seven steps of work).
                diagnostic = getattr(provider, 'contract_diagnostic', None) or {}
                kind = diagnostic.get('action_type')
                if kind not in RECOVERABLE and not str(exc).startswith('a worker action'):
                    raise
                if kind == 'invalid_json':
                    text = f"your last reply was not valid JSON ({diagnostic.get('reason', 'syntax error')} near character {diagnostic.get('position', '?')}). Reply with exactly ONE JSON object; escape quotes and newlines inside strings, and keep each file small."
                elif kind == 'incomplete':
                    text = 'your last reply was cut off before it ended: write a smaller file (split the work over several write_file steps if needed).'
                else:
                    text = str(exc)[:300]
                results.append({'step': step, 'tool': None, 'path': None, 'ok': False, 'result': text})
                self.store.event(rid, 'worker_step', {'package': directory, 'step': step, 'tool': None, 'path': None, 'ok': False, 'keys': [], 'summary': text[:300], 'unusable_reply': kind or 'rejected'})
                refused += 1
                if refused >= STUCK_REFUSALS:
                    self._stuck(rid, directory, step, str(STUCK_REFUSALS) + ' refused or pointless actions in a row', results)
                continue
            silent = 0
            try:
                outcome = workspace.apply(action)
            except Defer as wait:
                if defer:
                    defer(wait.needs)
                return 'deferred', results
            results.append({'step': step, 'tool': action.get('tool'), 'path': action.get('path'), 'ok': outcome['ok'], 'result': outcome['text'][:MAX_RESULT_CHARS]})
            if outcome.get('full'):
                workspace.full_text[step] = outcome['full']  # 回给模型的结果被截断到 2800 字符；完整的另存，recall_step 取这份 / the model saw 2800 characters; the full text is kept aside for recall_step
            self.store.event(rid, 'worker_step', {'package': directory, 'step': step, 'tool': action.get('tool'), 'path': action.get('path'), 'ok': outcome['ok'],
                                                  'keys': sorted(action), 'summary': outcome['text'][:300]})
            if outcome.get('sealed'):
                return 'sealed', results
            # 卡住检测：同一个失败连续出现，或连续做被拒绝/无意义的动作——继续烧步数没有意义，交给上层升级模型 / stuck detection: the same failure again, or refused actions in a row: more steps are wasted, escalate instead
            refused = refused + 1 if not outcome['ok'] and not outcome.get('signature') else 0
            tail = workspace.signatures[-STUCK_REPEATS:]
            if len(tail) == STUCK_REPEATS and len(set(tail)) == 1 and tail[0]:
                self._stuck(rid, directory, step, 'the same failure ' + str(STUCK_REPEATS) + ' checks in a row', results)
            if refused >= STUCK_REFUSALS:
                self._stuck(rid, directory, step, str(STUCK_REFUSALS) + ' refused or pointless actions in a row', results)
        return 'open', results

    def _generate_workers(self, rid, provider, goal, approved, metadata, retry_feedback=None):
        spec = approved['spec']
        files = dict(self.pack.seed_files(spec))
        planned = [f for f in spec['files'] if f['path'] not in files]
        queue = [(d, items, 1) for d, items in self._packages(spec, planned)]
        if len(queue) > 1:
            self.store.event(rid, 'package_order', {'order': [d for d, _, _ in queue], 'depends_on': {d: sorted(v) for d, v in getattr(self, '_edges', {}).items()}})
        sealed_before, kept = self._reusable(spec, files)
        files.update(kept)
        roles = RoleRuntime(self.store)
        sealed_dirs, notes_by_package, done, waiting = set(), {}, 0, {}
        self._initial = True
        total = len(queue)
        max_rounds = self._max_rounds(total)
        try:
            while queue:
                directory, items, round_no = queue.pop(0)
                if directory in sealed_before and round_no == 1:
                    sealed_dirs.add(directory)
                    done += 1
                    self.store.event(rid, 'package_reused', {'package': directory, 'files': sorted(i['path'] for i in items)})
                    continue
                metadata['gen_progress'] = {'completed': done, 'total': total, 'current': directory or '.'}
                self.planning.update(rid, metadata, 'created', 'package_worker_started')
                if metadata.get('status') in {'cancelled', 'failed'}:
                    raise MasaError(metadata.get('error', 'project generation cancelled'))
                workspace = WorkerWorkspace(self, rid, spec, approved, files, [i['path'] for i in items], directory)
                workspace.notes = notes_by_package.get(directory, '')
                workspace.sealed_dirs = sealed_dirs
                workspace.dependencies = set(getattr(self, '_edges', {}).get(directory, ()))
                workspace.goal, workspace.arbiter = goal, self._arbitrate
                # 还能让路吗：次数没用完，所等的包还在队列里，并且它们没有反过来在等本包（互相等待就谁也写不出来）。
                # May it still give way: rounds left, the packages it waits for are still queued, and none of them is waiting for this one (two packages waiting for each other would never be written).
                workspace.can_defer = lambda needs, directory=directory, round_no=round_no: (
                    round_no < max_rounds and any(d in needs for d, _, _ in queue) and not any(directory in waiting.get(n, ()) for n in needs))
                purposes = {i['path']: i['purpose'] for i in items}
                try:
                    def defer(needs, directory=directory, items=items, round_no=round_no, workspace=workspace):
                        notes_by_package[directory] = workspace.notes
                        waiting[directory] = set(needs)
                        self._defer(rid, queue, directory, items, round_no, needs)
                    outcome, results = self._worker_steps(rid, provider, roles, workspace, goal, spec, approved, directory, purposes, max_steps=MAX_STEPS, round_no=round_no,
                                                          prefix='w', retry_feedback=retry_feedback, defer=defer)
                    if outcome == 'deferred':
                        continue
                    if outcome != 'sealed':
                        last = results[-1]['result'] if results else ''
                        metadata['partial_files_ref'] = self.store.put(files)
                        raise MasaError(f'package {directory or "."} did not converge in {MAX_STEPS} steps; last result: {last[:600]}')
                    waiting.pop(directory, None)
                    self.store.event(rid, 'package_sealed', {'package': directory, 'steps': len(results), 'files': sorted(workspace.own_view())})
                except Stuck:
                    metadata['partial_files_ref'] = self.store.put(files)  # 带着写了一半的文件，让更强的模型接着做 / keep the half-written files for the stronger model
                    raise
                finally:
                    workspace.close()
                sealed_dirs.add(directory)
                done += 1
                metadata['partial_files_ref'] = self.store.put(files)
                metadata['gen_progress'] = {'completed': done, 'total': total, 'current': None}
                self.planning.update(rid, metadata, 'created', 'package_worker_done')
        finally:
            shutil.rmtree(Path(self.store.root) / 'v2-scratch', ignore_errors=True)
        return files

    # ───────────── 修复：只重开出问题的包 / repair: reopen only the packages that fail ─────────────
    def _repair_calls(self, approved):
        return MAX_REPAIR_STEPS + 2

    def _repair_targets(self, spec, files, evidence):
        """失败证据指向哪些包：证据里出现的实现文件（路径或点号模块名）所在的包，以及出现的测试文件所属的包。找不到就是全部包。
        Which packages the failure evidence points at: those owning an implementation file named in it (path or dotted module name), and the packages of the test files named in it. Nothing found: all packages."""
        text = json.dumps(evidence, ensure_ascii=False).replace(chr(92) * 2, '/').replace(chr(92), '/')
        sources = [p for p in files if self.pack.is_source(p) and p not in self.pack.seed_files(spec)]
        everything = {self.pack.package_of(p, spec) for p in sources}
        hit = set()
        for path in sources:
            dotted = path.rsplit('.', 1)[0].replace('/', '.')
            if path in text or re.search(r'(?<![\w.])' + re.escape(dotted) + r'(?!\w)', text):
                hit.add(self.pack.package_of(path, spec))
        for path in files:
            if self.pack.is_test(path) and path in text:
                package = self.pack.package_of(path, spec)
                if package in everything:
                    hit.add(package)
        return sorted(hit or everything), sorted(everything)

    def _propose_repair(self, rid, provider, data, approved, base, evidence, feedback, use_intelligence):
        spec = approved['spec']
        files = dict(base['files'])
        self._repairs = getattr(self, '_repairs', 0) + 1
        targets, everything = self._repair_targets(spec, files, evidence)
        widened = self._repairs >= 2  # 第二次起扩大到全部包：第一次只看出问题的包没修好，说明根因可能在别处 / from the second repair on, all packages: the first narrow repair did not fix it, so the cause may be elsewhere
        if widened:
            targets = everything
        seeds = self.pack.seed_files(spec)
        own = sorted(p for p in files if self.pack.is_source(p) and p not in seeds and self.pack.package_of(p, spec) in targets)
        if not own:
            raise MasaError('repair found no implementation file to change')
        purposes = {f['path']: f['purpose'] for f in spec['files'] if f['path'] in own}
        self.store.event(rid, 'repair_scope', {'packages': targets, 'all_packages': everything, 'files': own, 'widened': widened, 'repair_no': self._repairs})
        text = '\n'.join(str(item) for e in evidence for item in (e.get('diagnostics') or []))
        text = SCRATCH_PATH.sub('', text)[:2500]  # 验证目录的绝对路径是噪声 / the absolute verification directory is noise
        snippets = '\n'.join(f"{s['path']}:{s['line']}\n{s['source']}" for e in evidence for s in (e.get('snippets') or [])[:3])[:2000]
        extra = {'repair': {'failure': text + ('\n' + snippets if snippets else ''), 'feedback': str(feedback)[:2500], 'scope': targets}}
        # 失败测试函数的源码直接放进上下文：真实运行里 4 次修复有 3 次先花一步（约 3.6k token）去读测试文件。 The source of the failing test functions goes straight into the context: in real runs 3 of 4 repairs spent a first step (about 3.6k tokens) reading the test file.
        failing_tests = {}
        for e in evidence:
            for s in (e.get('snippets') or []):
                if len(failing_tests) < 3 and s.get('path') and self.pack.is_test(s['path']) and f"{s['path']}:{s['line']}" not in failing_tests:
                    source = self.pack.test_function_source(files, s['path'], s['line'])
                    if source:
                        failing_tests[f"{s['path']}:{s['line']}"] = source
        if failing_tests:
            extra['repair']['failing_tests'] = failing_tests
        workspace = WorkerWorkspace(self, rid, spec, approved, files, own, '')
        workspace.repair = True
        workspace.goal, workspace.arbiter = data['goal'], self._arbitrate  # 修复也用断言裁判（信号 S1 触发）/ repairs use the assertion referee too (triggered by signal S1)
        self._initial = True
        outcome, results, baseline, baseline_rank = 'open', [], None, None
        try:
            # 起点：先在草稿上真实跑一遍检查——给工人一份新鲜的、按“你的文件”整理过的失败清单，也给“有没有变好”一个基准。
            # Starting point: run the checks on the draft first - the worker gets a fresh failure list in terms of its files, and "did it get better" gets a baseline.
            first = workspace.run_check('test')
            if first.get('passed'):
                first = workspace.run_check('static')
            baseline = first.get('problems')
            baseline_rank = (bool(first.get('blocking')), baseline) if baseline is not None else None
            workspace.best = (baseline, {p: files[p] for p in own}) if baseline is not None else None
            workspace.best_rank = baseline_rank
            seed = [{'step': 0, 'tool': 'run_check', 'ok': first['ok'], 'path': None, 'result': first['text'][:MAX_RESULT_CHARS]}] if not first['ok'] else []
            try:
                outcome, results = self._worker_steps(rid, provider, RoleRuntime(self.store), workspace, data['goal'], spec, approved, '(repair)', purposes,
                                                      max_steps=MAX_REPAIR_STEPS, round_no=1, prefix='r', extra=extra, seed_results=seed)
            except Stuck:
                outcome = 'stuck'  # 原地打转：已有改善就把改善交出去；没有改善就“原样返回”（下面），由协调器当作“没有改动”：记一次失败、能升级就升级，到顶了就走诊断/改测试/停止，而不是让整个任务以异常失败（真实运行里 bank 的修复在最高等级卡住后任务直接失败）。
                # going in circles: hand over what improved; with no improvement return unchanged (below) and let the coordinator treat it as "no change": a recorded failure, escalation if possible, otherwise diagnosis / test revision / stop, instead of failing the whole task with an exception (in a real run a stuck repair at the top level failed the bank task outright).
        finally:
            workspace.close()
            shutil.rmtree(Path(self.store.root) / 'v2-scratch', ignore_errors=True)
        best = workspace.best
        # “变好”按同一个排序键比：没有阻塞性错误优先，再比问题数。只比数量会把“导入失败（1 个问题）”当成比“3 条断言失败”更好。
        # "Better" uses the same rank: no blocking errors first, then the problem count; counts alone would rate "an import failure (1 problem)" better than "3 failed assertions".
        improved = bool(best) and (baseline_rank is None or (workspace.best_rank or (True, best[0])) < baseline_rank)
        changes = {p: t for p, t in (best[1] if improved else {}).items() if t != base['files'].get(p)}
        self.store.event(rid, 'repair_worker_done', {'outcome': outcome, 'steps': len(results), 'baseline_problems': baseline, 'best_problems': best[0] if best else None,
                                                     'improved': improved, 'changed': sorted(changes)})
        if not changes:
            # 改了但没有哪个状态比起点好（或工人认为不用改）：原样返回，协调器按“没有改动”处理，而不是把变糟的版本交去验证。
            # Changed things but no state beat the starting point (or the worker saw nothing to change): return it unchanged, the coordinator handles it as a no-op instead of verifying a worse version.
            return base['files'], {own[0]: base['files'][own[0]]}
        return base['files'], changes

    def _defer(self, rid, queue, directory, items, round_no, needs):
        """把这个包推迟到它依赖的包之后。 Move this package behind the packages it needs."""
        positions = [i for i, (d, _, _) in enumerate(queue) if d in needs]
        at = (max(positions) + 1) if positions else len(queue)
        queue.insert(at, (directory, items, round_no + 1))
        self.store.event(rid, 'package_deferred', {'package': directory, 'waiting_for': sorted(needs), 'new_position': at})

    def _stuck(self, rid, directory, step, why, results):
        last = results[-1]['result'] if results else ''
        self.store.event(rid, 'worker_stuck', {'package': directory, 'step': step, 'why': why})
        raise Stuck(f'package {directory or "."} is stuck after {step} steps ({why}); last result: {last[:500]}')


class V2RepairOnly(V2Generation):
    """V1 的生成（一次便宜的整包草稿，或本地逐文件）+ V2 的修复（只重开出问题的包）。真实对照里 V1 生成在 grades 上只花 ¥0.05，但验证失败后用本地模型整包补丁既慢又弱；V2 的修复探针只花 ¥0.006–0.026。
    V1 generation (one cheap whole-bundle draft, or local file by file) plus V2 repair (reopen only the failing package). In real comparisons V1 generation cost only ¥0.05 on grades, but repairing a failed verification with whole-bundle patches from a local model was slow and weak, while the V2 repair probes cost ¥0.006-0.026."""
    workers = False

    def admission_need(self, goal, approved, growth):
        return ProjectGeneration.admission_need(self, goal, approved, growth)
