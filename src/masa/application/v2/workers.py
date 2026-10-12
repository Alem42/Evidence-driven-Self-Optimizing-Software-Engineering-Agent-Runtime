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
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from masa.application.checks.snippets import source_snippets
from masa.application.generation import ProjectGeneration
from masa.domain.models import MasaError
from masa.runtime.roles import RoleRuntime

MAX_STEPS = 16  # 每个包最多的模型步数（硬上限，写进预算）/ hard per-package step limit (also written into the budget)
MAX_FILE_BYTES = 60000
MAX_RESULT_CHARS = 1800  # 回给模型的单次工具结果上限 / cap on one tool result shown to the model
SOLO_FILES = 6  # 计划文件不多于这个数就合成一个包：小任务的测试常要启动整个程序，拆开会让“先写的包”永远等不到入口 / at most this many planned files make ONE package: small tasks' tests often start the whole program, and splitting makes the first package wait forever for the entry
STUCK_REPEATS = 3  # 同一个失败签名连续出现这么多次就判定卡住 / the same failure signature this many checks in a row means stuck
STUCK_REFUSALS = 4  # 连续这么多个被拒绝/无意义的动作也判定卡住 / this many refused or pointless actions in a row also means stuck
MAX_ROUNDS = 2  # 一个包最多被排进队列几次（第一次 + 因依赖未就绪被推迟一次）/ how many times a package may be scheduled (first time + one deferral for an unready dependency)
KEPT_RESULTS = 3  # 上下文里保留的最近工具往返数；更早的丢掉（笔记负责记住）/ recent tool round trips kept in the context; older ones are dropped (notes remember)


class Defer(Exception):
    """本包依赖还没写完的包：先去写那些包，再回来。 This package needs packages that are not written yet: do those first, then come back."""

    def __init__(self, needs):
        super().__init__(', '.join(sorted(needs)))
        self.needs = set(needs)


class Stuck(MasaError):
    """工人原地打转：同一个失败反复出现，或连续做无意义的动作。 The worker is going in circles: the same failure again and again, or pointless actions in a row."""


def signature(items):
    """一次检查的失败签名：去掉行号与数字，只留“哪个文件、什么问题”。 A check's failure signature: line numbers and digits stripped, only which file and what problem remain."""
    return tuple(sorted({re.sub(r'\d+', 'N', f"{i['path']}|{i['kind']}|{i['message'][:90]}") for i in items}))


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
    for directory, items in groups:
        text = ' '.join(i['purpose'] for i in items).lower()
        for other, tokens in names.items():
            if other != directory and any(re.search(r'(?<![a-z0-9_])' + re.escape(t) + r'(?![a-z0-9_])', text) for t in tokens):
                edges[directory].add(other)
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


def unmet_dependencies(pack, spec, files, items):
    """诊断里提到了“还没写出来的计划文件”所属的包吗？（例如 ModuleNotFoundError: No module named 'calc.parser.parser'）返回那些包。 Do the diagnostics mention a planned file that is not written yet? Returns the packages that own them."""
    text = ' '.join(i['message'] for i in items)
    needs = set()
    for item in spec['files']:
        path = item['path']
        if path in files or not pack.is_source(path):
            continue
        dotted = path.rsplit('.', 1)[0].replace('/', '.')
        directory = path.rsplit('/', 1)[0] if '/' in path else ''
        slashed = text.replace(chr(92), '/')
        # 点号模块名或文件路径；目录只在至少两级时才匹配（顶层目录名几乎出现在每条诊断里）。 A dotted module name or the file path; a directory counts only with two or more levels (a top-level name occurs in almost every diagnostic).
        by_directory = '/' in directory and re.search(r'(?<![\w/.])' + re.escape(directory) + r'(?![\w/])', slashed)
        if re.search(r'(?<![\w.])' + re.escape(dotted) + r'(?!\w)', text) or path in slashed or by_directory:
            needs.add(pack.package_of(path, spec))
    return needs


class WorkerWorkspace:
    """一个包工人看到的项目：所有文件的当前内容、本包可写的路径、一个供真实检查用的临时目录。 The project as one worker sees it."""

    def __init__(self, generation, rid, spec, approved, files, own_paths, directory):
        self.generation, self.rid, self.spec, self.approved = generation, rid, spec, approved
        self.pack = generation.pack
        self.files = files
        self.own_paths = set(own_paths)
        self.directory = directory
        self.notes = ''
        self.passed = set()  # 自上次修改以来通过的检查种类 / check kinds that passed since the last change
        self.signatures = []  # 每次失败检查的签名 / the signature of every failing check
        self.refused = 0  # 连续被拒绝/无意义的动作数 / refused or pointless actions in a row
        self.sealed_dirs = set()  # 已封包的包（由生成循环维护）/ sealed packages (kept by the generation loop)
        self.rounds_left = False  # 这个包还能不能被推迟（由生成循环设置）/ may this package still be deferred (set by the generation loop)
        self.scratch = Path(tempfile.mkdtemp(prefix='v2-', dir=self._scratch_root()))

    def _scratch_root(self):
        root = Path(self.generation.store.root) / 'v2-scratch'
        root.mkdir(parents=True, exist_ok=True)
        return root

    def close(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    # ───────────── 给模型看的视图 / the view shown to the model ─────────────
    def own_view(self):
        return {p: self.files[p] for p in sorted(self.own_paths) if p in self.files}

    def interfaces(self):
        """其它包已写文件的公开签名摘要（只读）。 Public signatures of other packages' written files (read-only)."""
        return {p: self.pack.outline(t) for p, t in sorted(self.files.items())
                if p not in self.own_paths and self.pack.is_source(p) and isinstance(t, str) and t.strip()}

    # ───────────── 工具 / tools ─────────────
    def read_file(self, path):
        if path in self.own_paths:
            if path not in self.files:
                return {'ok': True, 'text': f'{path} is not written yet'}
            return {'ok': True, 'text': 'your own files are always in your context (files): no need to read them'}
        if path in self.files and self.pack.is_source(path):
            return {'ok': True, 'text': 'public signatures only:' + chr(10) + self.pack.outline(self.files[path])}
        return {'ok': False, 'text': f'{path} is not available to you (another package, not written yet, or not a source file)'}

    def _check_write(self, path, content):
        if path not in self.own_paths:
            return {'ok': False, 'text': f'{path} is outside your package: you may only write {sorted(self.own_paths)}'}
        if not isinstance(content, str) or '\x00' in content or len(content.encode('utf-8')) > MAX_FILE_BYTES:
            return {'ok': False, 'text': 'the content must be text of at most 60 KB'}
        if not content.strip() and not self.pack.allow_empty(path):
            return {'ok': False, 'text': 'the file must not be empty'}
        return None

    def write_file(self, path, content):
        refusal = self._check_write(path, content)
        if refusal:
            return refusal
        candidate = self.generation._normalize({**self.files, path: content}, [path], self.rid)
        if self.files.get(path) == candidate[path]:
            return {'ok': False, 'text': f'{path} already has exactly this content: change something, run_check, or finish'}
        self.files[path] = candidate[path]
        self.passed.clear()
        problems = self.generation._syntax_errors(self.files, [path], self.spec, self.approved.get('contract'))
        text = f'wrote {path} ({len(self.files[path])} characters)'
        if path in problems:
            text += '. Quick local check found problems, fix them first:\n' + problems[path][:900]
        return {'ok': True, 'text': text, 'problems': path in problems}

    def edit_file(self, path, old, new):
        if path not in self.own_paths:
            return {'ok': False, 'text': f'{path} is outside your package'}
        current = self.files.get(path)
        if current is None:
            return {'ok': False, 'text': f'{path} does not exist yet: use write_file'}
        if not isinstance(old, str) or not old or current.count(old) != 1:
            return {'ok': False, 'text': f'"old" must occur exactly once in {path} (found {current.count(old) if isinstance(old, str) and old else 0})'}
        return self.write_file(path, current.replace(old, new if isinstance(new, str) else ''))

    def _materialize(self):
        for existing in list(self.scratch.rglob('*')):
            if existing.is_file():
                existing.unlink()
        for path, content in self.files.items():
            target = self.scratch / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode('utf-8'))  # 不经文本模式，Windows 不会把换行变成 CRLF / no text mode: Windows would turn newlines into CRLF

    def _is_own(self, path):
        return path in self.own_paths or (self.directory and path.startswith(self.directory + '/') and not self.pack.is_test(path))

    def run_check(self, kind):
        operation = self.pack.op_id(kind if kind in ('test', 'static') else 'test')
        if operation is None:
            return {'ok': False, 'text': f'this language has no {kind} check'}
        self._materialize()
        store, rid = self.generation.store, self.rid
        request = {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': 'v2-draft', 'operation': operation,
                   'timeout_ms': 90000, 'max_output_bytes': 131072}
        result = self.generation.executor.execute(request, self.scratch, lambda: bool(store.run(rid)['cancel_requested']))
        if result.get('status') != 'completed':
            return {'ok': False, 'text': f"the check did not complete: {result.get('status')} {str(result.get('error') or '')[:200]}"}
        if result.get('exit_code') == 0:
            return {'ok': True, 'text': f'{kind} check passed', 'passed': kind}
        # 临时目录的绝对路径换成 “.”：诊断里的路径才能还原成项目内的相对路径（否则 calc/app/app.py 会被当成“别的包的文件”）。
        # Replace the scratch directory's absolute path by ".": only then can diagnostic paths become project-relative (otherwise calc/app/app.py looks like another package's file).
        root = str(self.scratch)
        clean = {**result, **{key: str(result.get(key, '')).replace(root, '.').replace(root.replace(chr(92), '/'), '.') for key in ('stdout', 'stderr')}}
        items = self.pack.parse_failures([(operation, clean)])
        own = [i for i in items if i['path'] == '' or self._is_own(i['path'])]
        foreign = [i for i in items if i not in own]
        if not items:  # 没解析出任何诊断：给原始输出的尾巴 / nothing parsed: show the tail of the raw output
            tail = (str(clean.get('stderr', '')) + chr(10) + str(clean.get('stdout', ''))).strip()[-MAX_RESULT_CHARS:]
            return {'ok': False, 'text': 'the check failed (output could not be parsed):' + chr(10) + tail}
        if not own:
            return {'ok': True, 'text': f'{kind} check: no problem in your files ({len(foreign)} problem(s) belong to other packages, not yours)', 'foreign': len(foreign), 'passed': kind}
        # 失败签名与卡住提示：同一个失败连续出现，说明上一次改动没有用 / failure signature and stuck hint: the same failure again means the last change did not help
        sig = signature(own)
        repeated = bool(self.signatures) and self.signatures[-1] == sig
        self.signatures.append(sig)
        needs = unmet_dependencies(self.pack, self.spec, self.files, own) - {self.directory}
        if needs and not (needs & self.sealed_dirs) and self.rounds_left:
            raise Defer(needs)
        lines = [f"{i['path'] or '(test package)'}{':' + str(i['line']) if i['line'] else ''}: {i['message']}" for i in own[:10]]
        snippets = source_snippets(lines, self.files, pack=self.pack)[:3]
        text = f'{kind} check found {len(own)} problem(s) in your files:' + chr(10) + chr(10).join(lines)
        if snippets:
            text += chr(10) + chr(10).join(f"{s['path']}:{s['line']}" + chr(10) + s['source'] for s in snippets)
        if foreign:
            text += chr(10) + f'({len(foreign)} more problem(s) belong to other packages: not yours)'
        if repeated:
            text += chr(10) + 'WARNING: this is the same problem as the previous check, so your last change did not fix it. Do something different: re-read the failing assertion against the acceptance criteria and the examples.'
        return {'ok': False, 'text': text[:MAX_RESULT_CHARS], 'signature': sig, 'repeated': repeated}

    def finish(self):
        """封包：本包的测试文件存在、所有检查在本包文件上没有问题。 Seal: the package's tests exist and no check reports a problem in its files."""
        missing = sorted(p for p in self.own_paths if p not in self.files and not self.pack.allow_empty(p))
        if missing:
            return {'ok': False, 'text': 'these planned files are not written yet: ' + ', '.join(missing)}
        for kind in ('test', 'static'):
            if self.pack.op_id(kind) is None:
                continue
            outcome = self.run_check(kind)
            if not outcome['ok']:
                return {'ok': False, 'text': 'cannot finish yet. ' + outcome['text']}
        return {'ok': True, 'text': 'package sealed', 'sealed': True}

    def apply(self, action):
        tool = action.get('tool')
        if tool == 'read_file':
            return self.read_file(str(action.get('path', '')))
        if tool == 'write_file':
            return self.write_file(str(action.get('path', '')), action.get('content'))
        if tool == 'edit_file':
            return self.edit_file(str(action.get('path', '')), action.get('old'), action.get('new'))
        if tool == 'run_check':
            outcome = self.run_check(action.get('kind') or 'test')
            if outcome.get('passed'):
                self.passed.add(outcome['passed'])
                required = {k for k in ('test', 'static') if self.pack.op_id(k) is not None}
                complete = all(p in self.files or self.pack.allow_empty(p) for p in self.own_paths)
                if complete and required <= self.passed:  # 所有检查都在当前内容上通过：自动封包，不再依赖模型记得调用 finish / every check passed on the current content: seal automatically instead of relying on the model to call finish
                    return {'ok': True, 'text': outcome['text'] + '. All checks pass and all planned files exist: package sealed.', 'sealed': True}
            return outcome
        if tool == 'note':
            self.notes = str(action.get('text', ''))[:1000]
            return {'ok': True, 'text': 'noted'}
        if tool == 'finish':
            return self.finish()
        return {'ok': False, 'text': f'unknown tool {tool!r}'}


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
            sealed = {e['payload']['package'] for e in self.store.events(self.seed_run) if e['type'] == 'package_sealed'}
        except (MasaError, KeyError):
            return set(), {}
        return sealed, {p: t for p, t in kept.items() if p in {f['path'] for f in spec['files']} and p not in files}

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
        sealed_dirs, notes_by_package, done, first = set(), {}, 0, True
        total = len(queue)
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
                workspace.rounds_left = round_no < MAX_ROUNDS and any(d != directory for d, _, _ in queue)
                deferred = False
                try:
                    purposes = {i['path']: i['purpose'] for i in items}
                    results, sealed, refused = [], False, 0
                    for step in range(1, MAX_STEPS + 1):
                        context = {'goal': goal, 'acceptance': spec['acceptance'], 'examples': self._examples(approved), 'package': directory or '.',
                                   'own_files': [{'path': p, 'purpose': purposes[p]} for p in sorted(purposes)], 'files': workspace.own_view(),
                                   'interfaces': workspace.interfaces(), 'last_results': results[-KEPT_RESULTS:], 'notes': workspace.notes,
                                   'step': step, 'max_steps': MAX_STEPS, 'language_notes': self.pack.worker_notes(),
                                   **({'spec_examples': spec['examples']} if spec.get('examples') else {})}
                        if retry_feedback and step == 1 and round_no == 1:
                            context['previous_attempt_error'] = str(retry_feedback)[:1000]
                        invocation = 'initial' if first else f'w:{directory}:{round_no}:{step}'[:100]
                        first = False
                        action = roles.call(rid, provider, 'package_worker', context, invocation_id=invocation)
                        try:
                            outcome = workspace.apply(action)
                        except Defer as wait:
                            notes_by_package[directory] = workspace.notes
                            self._defer(rid, queue, directory, items, round_no, wait.needs)
                            deferred = True
                            break
                        results.append({'step': step, 'tool': action.get('tool'), 'path': action.get('path'), 'ok': outcome['ok'], 'result': outcome['text'][:MAX_RESULT_CHARS]})
                        self.store.event(rid, 'worker_step', {'package': directory, 'step': step, 'tool': action.get('tool'), 'path': action.get('path'), 'ok': outcome['ok'],
                                                              'summary': outcome['text'][:300]})
                        if outcome.get('sealed'):
                            sealed = True
                            break
                        # 卡住检测：同一个失败连续出现，或连续做被拒绝/无意义的动作——继续烧步数没有意义，交给上层升级模型 / stuck detection: the same failure again, or refused actions in a row: more steps are wasted, escalate instead
                        refused = refused + 1 if not outcome['ok'] and not outcome.get('signature') else 0
                        tail = workspace.signatures[-STUCK_REPEATS:]
                        if len(tail) == STUCK_REPEATS and len(set(tail)) == 1 and tail[0]:
                            self._stuck(rid, directory, step, 'the same failure ' + str(STUCK_REPEATS) + ' checks in a row', results)
                        if refused >= STUCK_REFUSALS:
                            self._stuck(rid, directory, step, str(STUCK_REFUSALS) + ' refused or pointless actions in a row', results)
                    if deferred:
                        continue
                    if not sealed:
                        last = results[-1]['result'] if results else ''
                        metadata['partial_files_ref'] = self.store.put(files)
                        raise MasaError(f'package {directory or "."} did not converge in {MAX_STEPS} steps; last result: {last[:600]}')
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

    def _defer(self, rid, queue, directory, items, round_no, needs):
        """把这个包推迟到它依赖的包之后（只推迟一次）。 Move this package behind the packages it needs (once only)."""
        positions = [i for i, (d, _, _) in enumerate(queue) if d in needs]
        at = (max(positions) + 1) if positions else len(queue)
        queue.insert(at, (directory, items, round_no + 1))
        self.store.event(rid, 'package_deferred', {'package': directory, 'waiting_for': sorted(needs), 'new_position': at})

    def _stuck(self, rid, directory, step, why, results):
        last = results[-1]['result'] if results else ''
        self.store.event(rid, 'worker_stuck', {'package': directory, 'step': step, 'why': why})
        raise Stuck(f'package {directory or "."} is stuck after {step} steps ({why}); last result: {last[:500]}')
