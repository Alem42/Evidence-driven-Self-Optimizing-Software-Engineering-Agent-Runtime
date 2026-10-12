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
KEPT_RESULTS = 3  # 上下文里保留的最近工具往返数；更早的丢掉（笔记负责记住）/ recent tool round trips kept in the context; older ones are dropped (notes remember)


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
        items = self.pack.parse_failures([(operation, result)])
        own = [i for i in items if i['path'] == '' or self._is_own(i['path'])]
        foreign = [i for i in items if i not in own]
        if not items:  # 没解析出任何诊断：给原始输出的尾巴 / nothing parsed: show the tail of the raw output
            tail = (str(result.get('stderr', '')) + chr(10) + str(result.get('stdout', ''))).strip()[-MAX_RESULT_CHARS:]
            return {'ok': False, 'text': 'the check failed (output could not be parsed):' + chr(10) + tail}
        if not own:
            return {'ok': True, 'text': f'{kind} check: no problem in your files ({len(foreign)} problem(s) belong to other packages, not yours)', 'foreign': len(foreign), 'passed': kind}
        lines = [f"{i['path'] or '(test package)'}{':' + str(i['line']) if i['line'] else ''}: {i['message']}" for i in own[:10]]
        snippets = source_snippets(lines, self.files, pack=self.pack)[:3]
        text = f'{kind} check found {len(own)} problem(s) in your files:' + chr(10) + chr(10).join(lines)
        if snippets:
            text += chr(10) + chr(10).join(f"{s['path']}:{s['line']}" + chr(10) + s['source'] for s in snippets)
        if foreign:
            text += chr(10) + f'({len(foreign)} more problem(s) belong to other packages: not yours)'
        return {'ok': False, 'text': text[:MAX_RESULT_CHARS]}

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

    def _packages(self, spec, planned):
        """按包分组：[(目录, [计划文件项])]；入口所在的目录最后。 Group planned files by package; the entry's directory comes last."""
        entry = self.pack.entrypoint(spec)
        if len(planned) <= SOLO_FILES:
            return [(self.pack.package_of(entry, spec), planned)] if planned else []
        groups = {}
        for item in planned:
            groups.setdefault(self.pack.package_of(item['path'], spec), []).append(item)
        entry_dir = self.pack.package_of(entry, spec)
        return sorted(groups.items(), key=lambda kv: (kv[0] == entry_dir, 0))

    def _call_budget(self, approved, local_files):
        planned = [f for f in approved['spec']['files'] if f['path'] not in self.pack.seed_files(approved['spec'])]
        return max(8, len({self.pack.package_of(f['path'], approved['spec']) for f in planned}) * (MAX_STEPS + 1))

    def _examples(self, approved):
        test_op = self.pack.op_id('test')
        for check in approved.get('checks', []):
            if check.get('operation') == test_op:
                return check.get('cases', [])
        return []

    def _generate_workers(self, rid, provider, goal, approved, metadata, retry_feedback=None):
        spec = approved['spec']
        files = dict(self.pack.seed_files(spec))
        planned = [f for f in spec['files'] if f['path'] not in files]
        groups = self._packages(spec, planned)
        roles = RoleRuntime(self.store)
        first = True
        try:
            for index, (directory, items) in enumerate(groups):
                metadata['gen_progress'] = {'completed': index, 'total': len(groups), 'current': directory or '.'}
                self.planning.update(rid, metadata, 'created', 'package_worker_started')
                if metadata.get('status') in {'cancelled', 'failed'}:
                    raise MasaError(metadata.get('error', 'project generation cancelled'))
                workspace = WorkerWorkspace(self, rid, spec, approved, files, [i['path'] for i in items], directory)
                try:
                    purposes = {i['path']: i['purpose'] for i in items}
                    results = []
                    sealed = False
                    for step in range(1, MAX_STEPS + 1):
                        context = {'goal': goal, 'acceptance': spec['acceptance'], 'examples': self._examples(approved), 'package': directory or '.',
                                   'own_files': [{'path': p, 'purpose': purposes[p]} for p in sorted(purposes)], 'files': workspace.own_view(),
                                   'interfaces': workspace.interfaces(), 'last_results': results[-KEPT_RESULTS:], 'notes': workspace.notes,
                                   'step': step, 'max_steps': MAX_STEPS, 'language_notes': self.pack.worker_notes()}
                        if retry_feedback and step == 1:
                            context['previous_attempt_error'] = str(retry_feedback)[:1000]
                        invocation = 'initial' if first else f'w:{directory}:{step}'[:100]
                        first = False
                        action = roles.call(rid, provider, 'package_worker', context, invocation_id=invocation)
                        outcome = workspace.apply(action)
                        results.append({'step': step, 'tool': action.get('tool'), 'path': action.get('path'), 'ok': outcome['ok'], 'result': outcome['text'][:MAX_RESULT_CHARS]})
                        self.store.event(rid, 'worker_step', {'package': directory, 'step': step, 'tool': action.get('tool'), 'path': action.get('path'), 'ok': outcome['ok'],
                                                              'summary': outcome['text'][:300]})
                        if outcome.get('sealed'):
                            sealed = True
                            break
                    if not sealed:
                        last = results[-1]['result'] if results else ''
                        raise MasaError(f'package {directory or "."} did not converge in {MAX_STEPS} steps; last result: {last[:600]}')
                    self.store.event(rid, 'package_sealed', {'package': directory, 'steps': len(results), 'files': sorted(workspace.own_view())})
                finally:
                    workspace.close()
                metadata['partial_files_ref'] = self.store.put(files)
                metadata['gen_progress'] = {'completed': index + 1, 'total': len(groups), 'current': None}
                self.planning.update(rid, metadata, 'created', 'package_worker_done')
        finally:
            shutil.rmtree(Path(self.store.root) / 'v2-scratch', ignore_errors=True)
        return files
