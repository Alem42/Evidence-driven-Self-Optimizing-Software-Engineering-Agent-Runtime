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
import tempfile
import time
import uuid
from pathlib import Path

from masa.application.checks import referee
from masa.application.checks.snippets import source_snippets
from masa.application.generation import ProjectGeneration
from masa.domain.models import MasaError, TransportFailure
from masa.runtime.roles import RoleRuntime

MAX_STEPS = 20  # 每个包最多的模型步数（硬上限，写进预算）/ hard per-package step limit (also written into the budget)
MAX_FILE_BYTES = 60000
MAX_RESULT_CHARS = 2800  # 回给模型的单次工具结果上限 / cap on one tool result shown to the model
SOLO_FILES = 6  # 计划文件不多于这个数就合成一个包：小任务的测试常要启动整个程序，拆开会让“先写的包”永远等不到入口 / at most this many planned files make ONE package: small tasks' tests often start the whole program, and splitting makes the first package wait forever for the entry
STUCK_REPEATS = 3  # 同一个失败签名连续出现这么多次就判定卡住 / the same failure signature this many checks in a row means stuck
STUCK_REFUSALS = 4  # 连续这么多个被拒绝/无意义的动作也判定卡住 / this many refused or pointless actions in a row also means stuck
MAX_ROUNDS = 2  # 没有依赖信息时一个包最多被排进队列几次；实际上限随包的个数增长（见 _max_rounds）/ how many times a package may be scheduled; the real limit grows with the number of packages (see _max_rounds)
MAX_REPAIR_STEPS = 12  # 一次修复里工人的最多步数 / worker steps in one repair
INTEGRATORS = frozenset({'app', 'main', 'cli', 'cmd', 'runner', 'driver', 'dispatcher', 'controller', 'service'})  # 名字表明它把别的包接起来：依赖所有其它包 / the name says it wires other packages together: it depends on all the others
RECOVERABLE = ('invalid_json', 'incomplete')  # 已收到但不可用的回答：不是传输故障，当作被拒绝的一步让模型重来 / received but unusable answers: not a transport failure, treated as a refused step so the model tries again
SCRATCH_PATH = re.compile(r'[A-Za-z]:[\\/][^"\n]*?[\\/]workspaces[\\/][0-9a-f]+[\\/]')  # 验证用的工作区绝对路径 / the absolute path of a verification workspace
FULL_INTERFACE_CHARS = 14000  # 直接依赖的包以全文（而不是只有签名）给工人，合计不超过这么多字符 / the packages a worker directly depends on are shown in full (not just signatures), within this many characters in total
WORKER_CONTEXT_CHARS = 8000  # 工人上下文里除目标、验收、示例以外的部分的估计上限（本包文件、签名、最近结果、语言说明）/ estimated upper bound of a worker context beyond goal, acceptance and examples (the package's files, signatures, recent results, language notes)
MAX_ARBITRATIONS = 2  # 每个包最多请几次断言裁判（每次约 3 个很小的调用）/ assertion referee rounds per package (about 3 tiny calls each)
TRANSPORT_GIVE_UP = 3  # 连续这么多步服务都没有回答就放弃 / give up after this many steps in a row without an answer
TRANSPORT_PAUSE = 3  # 第 n 次没有回答后等 n×这么多秒 / after the nth silent step wait n times this many seconds
KEPT_RESULTS = 3 # 上下文里保留的最近工具往返数；更早的丢掉（笔记负责记住）/ recent tool round trips kept in the context; older ones are dropped (notes remember)


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
        self.failed_check = None  # 上一次失败检查的种类；自那以后没改过文件才有值 / the kind of the last failing check, kept only while nothing was changed since
        self.last_tool = None  # 上一个动作的工具名 / the tool of the previous action
        self.exact = []  # 每次失败检查的逐字消息（不抹掉数字）：判断“实际值一点没变” / the verbatim messages of every failing check (digits kept): to tell that the actual value did not change at all
        self.signatures = []  # 每次失败检查的签名 / the signature of every failing check
        self.refused = 0  # 连续被拒绝/无意义的动作数 / refused or pointless actions in a row
        self.sealed_dirs = set()  # 已封包的包（由生成循环维护）/ sealed packages (kept by the generation loop)
        self.rounds_left = False  # 这个包还能不能被推迟（由生成循环设置）/ may this package still be deferred (set by the generation loop)
        self.can_defer = lambda needs: self.rounds_left  # 生成循环可以换成按“等谁”判断的函数 / the generation loop may replace it with a function that looks at who is waited for
        self.repair = False  # 修复模式：测试冻结、所有失败都算自己的 / repair mode: tests are frozen and every failure counts as the worker's own
        self.goal = ''  # 任务目标原文（断言裁判要用）/ the task's goal text (the assertion referee needs it)
        self.arbiter = None  # 断言裁判（由生成循环设置）：失败的断言一再出现时请它判断是测试错还是实现错 / the assertion referee (set by the generation loop): asked whether the test or the code is wrong when a failing assertion keeps coming back
        self.arbitrations = 0
        self.dependencies = set()  # 本包直接依赖的包（由生成循环设置）/ the packages this one directly depends on (set by the generation loop)
        self.best = None  # 修复模式：到目前为止检查结果最好的状态 (问题数, {路径: 内容}) / repair mode: the best state seen so far (problems, {path: text})
        self.changed = False  # 修复模式里已经改过实现文件了吗 / has an implementation file been changed yet (repair mode)
        self.test_writes = 0  # 修复模式里被拒绝的写测试尝试 / refused attempts to write a test in repair mode
        self.shown = set()  # 工人读过的别的包的文件（之后以全文留在 interfaces 里）/ other packages' files the worker has read (kept in full in interfaces from then on)
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
        """其它包已写文件的只读视图：本包直接依赖的包给全文（返回值、异常、副作用都看得见；真实运行里 app 工人只看签名，猜错了 store 的返回类型，16 步修不好），其余只给公开签名。
        Read-only view of other packages' written files: the packages this one directly depends on in full (return values, exceptions and side effects are visible; in a real run the app worker saw signatures only, guessed the store's return type wrong and could not fix it in 16 steps), everything else as public signatures."""
        budget, out = FULL_INTERFACE_CHARS, {}
        for path, text in sorted(self.files.items()):
            if path in self.own_paths or not isinstance(text, str) or not text.strip():
                continue
            if not self.pack.is_source(path) and not (self.repair and path in self.shown and self.pack.is_test(path)):
                continue  # 测试只在修复模式里、被读过之后才出现（修复工人必须看得到冻结的失败测试）/ tests appear only in repair mode and only after being read (a repair worker must be able to see the frozen failing test)
            if (path in self.shown or (self.dependencies and self.pack.package_of(path, self.spec) in self.dependencies)) and len(text) <= budget:
                out[path] = text
                budget -= len(text)
            else:
                out[path] = self.pack.outline(text)
        return out

    # ───────────── 工具 / tools ─────────────
    def allowed_tools(self):
        """这一步可以用哪些工具：什么都没写之前只能写文件；文件没写全不能 finish；修复里没改过任何东西不能 finish。 Which tools this step may use: only write_file before anything exists, no finish while planned files are missing or (repair) nothing was changed."""
        everything = ['write_file', 'edit_file', 'read_file', 'run_check', 'note', 'finish']
        if self.failed_check:
            return ['edit_file', 'write_file', 'read_file', 'note']  # 上次检查失败后还没改过东西：只能改 / the last check failed and nothing was changed yet: only changing is useful
        if self.repair:
            return everything if self.changed else ['edit_file', 'write_file', 'read_file', 'note']
        written = [p for p in self.own_paths if p in self.files and (self.files[p].strip() or self.pack.allow_empty(p))]
        if not written:
            return ['write_file']
        if any(p not in self.files and not self.pack.allow_empty(p) for p in self.own_paths):
            return [t for t in everything if t != 'finish']
        return everything

    def read_file(self, path):
        # 读文件不改变任何东西：没有新信息的读取（自己的文件已经在上下文里、同一个别的包的文件又读一遍）算“无意义的动作”（ok=False），连续出现会触发卡住检测；并且告诉模型下一步该做什么。
        # A read changes nothing: a read without new information (your own file is already in the context, another package's file a second time) is a pointless action (ok=False) that feeds stuck detection, and the answer says what to do next.
        if path in self.own_paths:
            if path not in self.files:
                return {'ok': True, 'text': f'{path} is not written yet: write it with write_file'}
            return {'ok': False, 'text': 'your own files are always in your context (files): reading them again gains nothing. Next: edit_file or write_file to change something, or run_check.'}
        if path in self.files and (self.pack.is_source(path) or (self.repair and self.pack.is_test(path))):
            if path in self.shown:
                return {'ok': False, 'text': f'you already read {path} (its full source is in interfaces): reading again gains nothing. Next: write_file or run_check.'}
            self.shown.add(path)
            # 别的包的文件读一次就给全文，之后一直留在 interfaces 里（上下文只保留最近 3 次工具结果，工人读过的东西不该再丢）；只有写它们是被禁止的。
            # Another package's file is given in full when read and stays in interfaces from then on (the context keeps only the last 3 tool results and what the worker asked for must not be lost); only writing them is forbidden.
            return {'ok': True, 'text': f'{path} (read-only, it stays in interfaces from now on):' + chr(10) + self.files[path][:MAX_RESULT_CHARS]}
        return {'ok': False, 'text': f'{path} is not available to you (another package, not written yet, or not a source file). Use only the paths in interfaces and own_files.'}

    def _check_write(self, path, content):
        if self.repair and self.pack.is_test(path):
            self.test_writes += 1
            if self.test_writes >= 2:
                raise MasaError('repair cannot change tests, module or file structure: the worker insists the test expectations are wrong')
            return {'ok': False, 'text': f'{path} is a frozen test: in a repair you may change implementation files only. Fix the implementation so the test passes; another step of the harness revises tests when a test really contradicts the acceptance criteria.'}
        if path not in self.own_paths:
            return {'ok': False, 'text': f'{path} is outside your package: you may only write {sorted(self.own_paths)}'}
        if not isinstance(content, str) or '\x00' in content or len(content.encode('utf-8')) > MAX_FILE_BYTES:
            if not isinstance(content, str):
                return {'ok': False, 'text': 'write_file needs a "content" field holding the complete file text (a JSON string); your reply had none or a different type. Reply again with {"tool":"write_file","path":...,"content":"..."}'}
            return {'ok': False, 'text': 'the content must be text without NUL characters and at most 60 KB'}
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
        self.changed = True
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
        # 只算本包自己的目录（不含嵌套的子包）：入口包 bankledger 的前缀会把所有子包的文件都算成“自己的”，于是它一直在修别的包的失败（真实运行里 13 步全耗在 parser 的错误上）。
        # Only the package's own directory, not nested packages: the entry package's prefix `bankledger/` would make every sub-package file "its own" and it kept chasing another package's failure (13 steps in a real run).
        return path in self.own_paths or (bool(self.directory) and path.rsplit('/', 1)[0] == self.directory and not self.pack.is_test(path))

    def run_check(self, kind):
        operation = self.pack.op_id(kind if kind in ('test', 'static') else 'test')
        if operation is None:
            return {'ok': False, 'text': f'this language has no {kind} check'}
        if kind == 'test' and not any(self.pack.is_test(p) for p in self.files):
            # 还没有任何测试文件：测试运行器会以一个无法解析的回溯失败，工人读不懂（真实运行里它为此烧光了所有步数）。直接说清楚该做什么。
            # There is no test file at all yet: the test runner would fail with an unparseable traceback the worker cannot read (in a real run it burnt every step on that). Say what to do instead.
            own_tests = [p for p in self.own_paths if self.pack.is_test(p)]
            if own_tests:
                return {'ok': False, 'problems': 1, 'text': 'there is no test file yet: write your package tests first (' + ', '.join(sorted(own_tests)) + '), then run_check.'}
            return {'ok': True, 'passed': kind, 'problems': 0, 'text': 'no test file exists in the project yet, so there is nothing to run'}
        self._materialize()
        store, rid = self.generation.store, self.rid
        request = {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': 'v2-draft', 'operation': operation,
                   'timeout_ms': 90000, 'max_output_bytes': 131072}
        result = self.generation.executor.execute(request, self.scratch, lambda: bool(store.run(rid)['cancel_requested']))
        if result.get('status') != 'completed':
            return {'ok': False, 'problems': None, 'text': f"the check did not complete: {result.get('status')} {str(result.get('error') or '')[:200]}"}
        if result.get('exit_code') == 0:
            return {'ok': True, 'text': f'{kind} check passed', 'passed': kind, 'problems': 0}
        # 临时目录的绝对路径换成 “.”：诊断里的路径才能还原成项目内的相对路径（否则 calc/app/app.py 会被当成“别的包的文件”）。
        # Replace the scratch directory's absolute path by ".": only then can diagnostic paths become project-relative (otherwise calc/app/app.py looks like another package's file).
        root = str(self.scratch)
        clean = {**result, **{key: str(result.get(key, '')).replace(root, '.').replace(root.replace(chr(92), '/'), '.') for key in ('stdout', 'stderr')}}
        items = self.pack.parse_failures([(operation, clean)])
        own = list(items) if self.repair else [i for i in items if i['path'] == '' or self._is_own(i['path'])]
        foreign = [i for i in items if i not in own]
        if not items:  # 没解析出任何诊断：给原始输出的尾巴 / nothing parsed: show the tail of the raw output
            tail = (str(clean.get('stderr', '')) + chr(10) + str(clean.get('stdout', ''))).strip()[-MAX_RESULT_CHARS:]
            return {'ok': False, 'problems': 1, 'text': 'the check failed (output could not be parsed):' + chr(10) + tail}
        if not own:
            return {'ok': True, 'text': f'{kind} check: no problem in your files ({len(foreign)} problem(s) belong to other packages, not yours)', 'foreign': len(foreign), 'passed': kind, 'problems': 0}
        # 失败签名与卡住提示：同一个失败连续出现，说明上一次改动没有用 / failure signature and stuck hint: the same failure again means the last change did not help
        sig = signature(own)
        repeated = bool(self.signatures) and self.signatures[-1] == sig
        self.signatures.append(sig)
        verbatim = tuple(sorted(f"{i['path']}|{i['message']}" for i in own))
        unchanged = bool(self.exact) and self.exact[-1] == verbatim
        self.exact.append(verbatim)
        needs = unmet_dependencies(self.pack, self.spec, self.files, own) - {self.directory}
        if needs and not (needs & self.sealed_dirs) and self.can_defer(needs):
            raise Defer(needs)
        lines = [f"{i['path'] or '(test package)'}{':' + str(i['line']) if i['line'] else ''}: {i['message']}" for i in own[:10]]
        snippets = source_snippets(lines, self.files, pack=self.pack)[:3]
        text = f'{kind} check found {len(own)} problem(s) in your files:' + chr(10) + chr(10).join(lines)
        detail = self.pack.failure_details([(operation, clean)], 1200)
        if detail:  # 期望 vs 实际的逐行差异：异常行里被缩写成 [21 chars] 的部分在这里看得到 / the line diff of expected vs actual: what the exception line abbreviates to [21 chars] is visible here
            text += chr(10) + 'details of the first failures (in the line diff "-" lines belong to the first argument of assertEqual = the ACTUAL value of your code, "+" lines to the second = the EXPECTED value written in the test):' + chr(10) + detail
        if snippets:
            text += chr(10) + chr(10).join(f"{s['path']}:{s['line']}" + chr(10) + s['source'] for s in snippets)
        if any(i['kind'] == 'assertion' for i in own) and not self.repair:
            text += chr(10) + 'For each failing assertion decide which side contradicts the acceptance criteria - the expected value in the test (fix the test) or the actual behaviour of the code (fix the code) - and change only that side.'
        if foreign:
            text += chr(10) + f'({len(foreign)} more problem(s) belong to other packages: not yours)'
        if unchanged and any(i['kind'] == 'assertion' for i in own) and not self.repair:
            # 真实运行（grades）：测试里的期望值算错了（89*7+92 写成 91.125），工人五次尝试都只改实现，实际值一点没变。
            # Real run (grades): the test's expected value was computed wrongly (89*7+92 written as 91.125) and the worker changed only the implementation in five attempts while the actual value never moved.
            text += chr(10) + 'The ACTUAL value is exactly the same as in the previous check although you changed code, so your code changes have no effect on this assertion. Recompute the EXPECTED value by hand from the inputs of that test and the acceptance criteria (write the arithmetic in a note). If the expectation is wrong, fix the TEST (it is your file).'
        if (unchanged or repeated) and self.arbiter and not self.repair and self.arbitrations < MAX_ARBITRATIONS and any(i['kind'] == 'assertion' for i in own):
            # 同一个断言一再失败：工人自己判断不了是测试错还是实现错（真实运行里 5 次尝试都在改对的那一边）。让独立的裁判在打乱成 A/B 的两个值里选，结论放在结果最前面。
            # The same assertion keeps failing and the worker cannot tell which side is wrong (in a real run five attempts kept changing the correct side): an independent referee chooses between the two values shuffled into A/B, the verdict goes first in the result.
            self.arbitrations += 1
            verdict = self.arbiter(self, [(operation, clean)])
            if verdict:
                text = verdict + chr(10) + text
        if repeated:
            text += chr(10) + 'WARNING: this is the same problem as the previous check, so your last change did not fix it. Do something different: re-read the failing assertion against the acceptance criteria and the examples.'
        return {'ok': False, 'text': text[:MAX_RESULT_CHARS], 'signature': sig, 'repeated': repeated, 'problems': len(own)}

    def _record(self, problems):
        """修复模式：记住检查结果最好的状态（问题数最少；同样多取先到的）。工人可能越改越糟，最后交出去的必须是最好的那个状态而不是最后的状态。
        Repair mode: remember the best state seen (fewest problems, the earlier one on a tie). A worker can make things worse by editing on; what is handed over must be the best state, not the last one."""
        if not self.repair or problems is None:
            return
        if self.best is None or problems < self.best[0]:
            self.best = (problems, {p: self.files[p] for p in self.own_paths if p in self.files})

    def finish(self):
        """封包：本包的测试文件存在、所有检查在本包文件上没有问题。 Seal: the package's tests exist and no check reports a problem in its files."""
        missing = sorted(p for p in self.own_paths if p not in self.files and not self.pack.allow_empty(p))
        if missing:
            first = missing[0]
            return {'ok': False, 'text': 'these planned files are not written yet: ' + ', '.join(missing) + '. You cannot finish before writing them. Your next reply must be exactly: {"tool":"write_file","path":"' + first + '","content":"<the complete file text>"}'}
        if self.repair and not self.changed:  # 真实运行里修复工人第一步就 finish，连续三次，什么都没改 / in a real run the repair worker answered finish at once, three times, changing nothing
            return {'ok': False, 'text': 'you have not changed anything yet and the checks fail (see repair.failure). Change an implementation file in own_files first with edit_file or write_file, then run_check.'}
        for kind in ('test', 'static'):
            if self.pack.op_id(kind) is None:
                continue
            outcome = self.run_check(kind)
            if not outcome['ok']:
                return {'ok': False, 'text': 'cannot finish yet. ' + outcome['text'], 'signature': outcome.get('signature'), 'problems': outcome.get('problems')}
        return {'ok': True, 'text': 'package sealed', 'sealed': True}

    def apply(self, action):
        tool = action.get('tool')
        previous, self.last_tool = self.last_tool, tool
        self.last_tool = previous  # 笔记检查要看“上一个”动作，用完再更新 / the note check needs the previous action; updated after the call
        try:
            if tool in ('run_check', 'finish') and self.failed_check and (tool == 'finish' or (action.get('kind') or 'test') == self.failed_check):
                # 没改任何东西就再跑一遍同一个检查，只会得到同样的失败（真实运行里本地模型连续 finish 了好几次，十分钟就这样过去了）。
                # Running the same check again without a change gives the same failure (in a real run the local model called finish several times in a row and ten minutes went by).
                return {'ok': False, 'text': 'nothing was changed since the last failing check, so it would fail in the same way. Change a file first with edit_file or write_file (read the problems above), then run_check.'}
            outcome = self._apply(action)
            if tool in ('write_file', 'edit_file') and outcome.get('ok'):
                self.failed_check = None
                if self.repair and not outcome.get('problems'):
                    # 修复模式每次改完文件，工具自己立刻跑检查并把结果接在后面：真实探针里 Flash 连改了 12 次文件一次也没去跑检查，没有反馈就是盲改，也记不下“最好的状态”。
                    # In repair mode the tool runs the check itself right after every change and appends the result: in a real probe Flash changed files 12 times without ever running a check - blind edits, and no best state to remember.
                    check = self._apply({'tool': 'run_check', 'kind': 'test'})
                    self._record(check.get('problems') if not check.get('passed') else None)
                    if check.get('sealed'):
                        self._record(0)
                    outcome = {**outcome, 'text': (outcome['text'] + chr(10) + 'automatic check after your change: ' + check['text'])[:MAX_RESULT_CHARS + 400], 'sealed': check.get('sealed'), 'signature': check.get('signature')}
                    self.failed_check = 'test' if (not check.get('ok') and check.get('signature')) else None
            elif tool in ('run_check', 'finish') and not outcome.get('ok') and outcome.get('signature'):
                self.failed_check = 'test' if tool == 'finish' else (action.get('kind') or 'test')
            elif tool in ('run_check', 'finish') and outcome.get('ok'):
                self.failed_check = None
            return outcome
        finally:
            self.last_tool = tool

    def _apply(self, action):
        tool = action.get('tool')
        if tool == 'read_file':
            return self.read_file(str(action.get('path', '')))
        if tool == 'write_file':
            return self.write_file(str(action.get('path', '')), action.get('content'))
        if tool == 'edit_file':
            return self.edit_file(str(action.get('path', '')), action.get('old'), action.get('new'))
        if tool == 'run_check':
            outcome = self.run_check(action.get('kind') or 'test')
            self._record(outcome.get('problems') if not outcome.get('passed') else None)
            if outcome.get('passed'):
                self.passed.add(outcome['passed'])
                required = [k for k in ('test', 'static') if self.pack.op_id(k) is not None]
                complete = all(p in self.files or self.pack.allow_empty(p) for p in self.own_paths)
                # 一个检查通过、文件都在：另一个必需的检查由工具自己跑，不让模型再花一步去想起它（真实运行里模型测试通过后一直读文件、重写，从没去跑静态检查，到步数上限也没封包）。
                # One check passed and every planned file exists: the tool runs the other required check itself instead of spending a model step on remembering it (in a real run the model kept re-reading and rewriting after the tests passed and never ran the static check).
                for kind in required if complete else []:
                    if kind in self.passed:
                        continue
                    extra = self.run_check(kind)
                    if not extra.get('passed'):
                        self._record(extra.get('problems'))
                        return {**extra, 'text': outcome['text'] + chr(10) + extra['text'], 'ok': False}
                    self.passed.add(kind)
                if complete and set(required) <= self.passed:
                    self._record(0)
                if complete and set(required) <= self.passed:  # 所有检查都在当前内容上通过：自动封包，不再依赖模型记得调用 finish / every check passed on the current content: seal automatically instead of relying on the model to call finish
                    return {'ok': True, 'text': outcome['text'] + '. All checks pass and all planned files exist: package sealed.', 'sealed': True}
            return outcome
        if tool == 'note':
            if self.last_tool == 'note':  # 连着记笔记不会让任何东西变好（真实运行里三个 note 连续，步数就这样耗光了）/ writing notes back to back improves nothing (in a real run three notes in a row burnt the steps)
                return {'ok': False, 'text': 'you just wrote a note: act now - edit_file or write_file to change something, or run_check.'}
            self.notes = str(action.get('text', ''))[:1000]
            return {'ok': True, 'text': 'noted'}
        if tool == 'finish':
            outcome = self.finish()
            if outcome.get('sealed'):
                self._record(0)
            return outcome
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

    def _context(self, workspace, goal, spec, approved, directory, purposes, results, step, max_steps, extra=None):
        context = {'goal': goal, 'acceptance': spec['acceptance'], 'examples': self._examples(approved), 'package': directory or '.',
                   'own_files': [{'path': p, 'purpose': purposes.get(p, '')} for p in sorted(purposes)], 'files': workspace.own_view(),
                   'interfaces': workspace.interfaces(), 'last_results': results[-KEPT_RESULTS:], 'notes': workspace.notes,
                   'step': step, 'max_steps': max_steps, 'language_notes': self.pack.worker_notes(),
                   **({'spec_examples': spec['examples']} if spec.get('examples') else {})}
        context.update(extra or {})
        allowed = workspace.allowed_tools()
        if len(allowed) < 6:
            context['allowed_tools'] = allowed
        return context

    def _worker_steps(self, rid, provider, roles, workspace, goal, spec, approved, directory, purposes, *, max_steps, round_no, prefix, retry_feedback=None, extra=None, defer=None, seed_results=None):
        """一个工人的循环：返回 (封包了吗, 步骤记录)；被推迟时抛出 Defer 的处理交给 defer 回调，返回 ('deferred', …)；卡住抛 Stuck。
        One worker's loop: returns (outcome, results) where outcome is 'sealed', 'deferred' or 'open' (the steps ran out); a stuck worker raises Stuck."""
        results, refused, silent = list(seed_results or []), 0, 0
        for step in range(1, max_steps + 1):
            context = self._context(workspace, goal, spec, approved, directory, purposes, results, step, max_steps, extra)
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
        workspace = WorkerWorkspace(self, rid, spec, approved, files, own, '')
        workspace.repair = True
        self._initial = True
        outcome, results, baseline = 'open', [], None
        try:
            # 起点：先在草稿上真实跑一遍检查——给工人一份新鲜的、按“你的文件”整理过的失败清单，也给“有没有变好”一个基准。
            # Starting point: run the checks on the draft first - the worker gets a fresh failure list in terms of its files, and "did it get better" gets a baseline.
            first = workspace.run_check('test')
            if first.get('passed'):
                first = workspace.run_check('static')
            baseline = first.get('problems')
            workspace.best = (baseline, {p: files[p] for p in own}) if baseline is not None else None
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
        improved = bool(best) and (baseline is None or best[0] < baseline)
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
