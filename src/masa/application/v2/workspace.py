"""包工人看到的工作区与它的工具：文件视图、读写改、跑真实检查、封包。 The worker's workspace and its tools: the file view, read/write/edit, running the real checks and sealing."""
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from masa.application.checks.snippets import source_snippets
from masa.domain.models import ExpectationSuspected, MasaError

MAX_FILE_BYTES = 60000
MAX_RESULT_CHARS = 2800  # 回给模型的单次工具结果上限 / cap on one tool result shown to the model
RECALL_CHARS = 8000  # recall_step 取回的上限（检查的完整输出另存）/ cap of what recall_step returns (a check's full output is kept aside)
FULL_INTERFACE_CHARS = 14000  # 直接依赖的包以全文（而不是只有签名）给工人，合计不超过这么多字符 / the packages a worker directly depends on are shown in full (not just signatures), within this many characters in total
MAX_ARBITRATIONS = 2  # 每个包最多请几次断言裁判（每次约 3 个很小的调用）/ assertion referee rounds per package (about 3 tiny calls each)


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
        self.edits = 0  # 已成功写入的次数 / how many writes succeeded
        self.edit_marks = []  # 每次失败检查当时的写入次数（与 exact 平行）：判断“实际值没变”之间到底改过几次 / the write count at every failing check (parallel to exact): how often the code changed between two observations
        self.last_referee = None  # 最近一次断言裁判的逐条结论 / the referee's latest per-case verdicts
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
        self.history = []  # 这个循环里每一步的结果（由生成循环设置；摘要行与 recall_step 的来源）/ the result of every step of this loop (set by the generation loop; the source of the digest lines and recall_step)
        self.verdicts = []  # 断言裁判的结论（钉住项用）/ the referee's verdicts (pinned)
        self.verdict_edits = []  # 每条裁判结论给出时的写入次数（与 verdicts 平行）：之后代码又改了几次，决定它还算不算“现在的事实” / the write count when each verdict was given (parallel to verdicts): how often the code changed since decides whether it is still "true now"
        self.full_text = {}  # 步号 → 该步检查结果的完整文本（回给模型的那份被截断到 2800 字符；recall_step 取回这份）/ step -> the full text of that step's check (the copy shown to the model is cut to 2800 characters; recall_step returns this one)
        self.failed_kind = None  # 当前失败连续属于哪种检查 / which kind of check the current failure streak belongs to
        self.best_rank = None  # 最好状态的排序键 (有无阻塞错误, 问题数) / the ranking key of the best state: (has blocking errors, problems)
        self.memory = None  # 当前的记忆策略（由生成循环设置；None = window）/ the current memory strategy (set by the generation loop; None = window)
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
        if self.can_recall():
            everything.append('recall_step')  # 旧结果已经离开原文窗口：允许按步号回查 / older results have left the verbatim window: recall by step number is allowed
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

    def actual_stable(self):
        """信号 S1：同一批失败断言的实际值连续 3 次观察完全相同，其间代码至少改过 2 次。 Signal S1: identical actual values in 3 observations in a row with at least 2 code changes in between."""
        return len(self.exact) >= 3 and self.exact[-1] == self.exact[-2] == self.exact[-3] and self.edit_marks[-1] - self.edit_marks[-3] >= 2

    def can_recall(self):
        return bool(self.memory is not None and self.memory.recall and len(self.history) > self.memory.keep)

    def recall_step(self, step):
        """按步号取回一个旧结果的原文（来自本循环的记录；不重新执行任何东西）。 Return an older result in full by step number (from this loop's record; nothing is re-executed)."""
        if self.last_tool == 'recall_step':  # 连着回查不会让任何东西变好 / recalling twice in a row improves nothing
            return {'ok': False, 'text': 'you just recalled a result: act now - edit_file or write_file to change something, or run_check.'}
        if not self.can_recall():
            return {'ok': False, 'text': 'recall_step is not available: no result has left the verbatim window yet.'}
        record = next((r for r in self.history if r.get('step') == step), None)
        if record is None or isinstance(step, bool):
            return {'ok': False, 'text': f'there is no step {step!r} in this loop: use a step number from progress_digest.'}
        # 账本无损、窗口有损：检查的完整输出另存一份，回查时给这一份（回给模型的结果被截断到 2800 字符）。 The ledger is lossless, the window is lossy: a check's full output is kept aside and recalled from there (the copy shown to the model is cut to 2800 characters).
        body = str(getattr(self, 'full_text', {}).get(step) or record.get('result', ''))[:RECALL_CHARS]
        return {'ok': True, 'text': f"step {record['step']} ({record.get('tool') or 'reply'}{' ' + str(record['path']) if record.get('path') else ''}, {'ok' if record.get('ok') else 'failed'}):" + chr(10) + body}

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
        self.edits += 1
        self.passed.clear()
        problems = self.generation._syntax_errors(self.files, [path], self.spec)
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
            self._streak_ends(kind)
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
            self._streak_ends(kind)
            return {'ok': True, 'text': f'{kind} check: no problem in your files ({len(foreign)} problem(s) belong to other packages, not yours)', 'foreign': len(foreign), 'passed': kind, 'problems': 0}
        # 失败签名与卡住提示：同一个失败连续出现，说明上一次改动没有用 / failure signature and stuck hint: the same failure again means the last change did not help
        sig = signature(own)
        repeated = bool(self.signatures) and self.signatures[-1] == sig
        self.signatures.append(sig)
        self.failed_kind = kind
        verbatim = tuple(sorted(f"{i['path']}|{i['message']}" for i in own))
        unchanged = bool(self.exact) and self.exact[-1] == verbatim
        self.exact.append(verbatim)
        self.edit_marks.append(self.edits)
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
        # 触发：生成时沿用“同一断言重复失败”；修复时用信号 S1“实际值稳定”——同一批断言的实际值连续 3 次观察完全相同，其间代码改过至少 2 次（改了代码，输出却一点没变）。
        # Trigger: when generating, "the same assertion failed again"; when repairing, signal S1 "the actual value is stable" - the actual values of the same assertions were identical in 3 observations in a row although the code changed at least twice in between.
        if (self.actual_stable() if self.repair else (unchanged or repeated)) and self.arbiter and self.arbitrations < MAX_ARBITRATIONS and any(i['kind'] == 'assertion' for i in own):
            # 同一个断言一再失败：工人自己判断不了是测试错还是实现错（真实运行里 5 次尝试都在改对的那一边）。让独立的裁判在打乱成 A/B 的两个值里选，结论放在结果最前面。
            # The same assertion keeps failing and the worker cannot tell which side is wrong (in a real run five attempts kept changing the correct side): an independent referee chooses between the two values shuffled into A/B, the verdict goes first in the result.
            self.arbitrations += 1
            verdict = self.arbiter(self, [(operation, clean)])
            if verdict:
                self.verdicts.append(verdict)
                self.verdict_edits.append(self.edits)
                text = verdict + chr(10) + text
                votes = [v['verdict'] for v in (self.last_referee or {}).values()]
                if self.repair and 'test_wrong' in votes and 'implementation_wrong' not in votes:
                    # 裁判认为测试期望错了：继续改实现只会把实现带歪（真实运行里工人为迎合错测试加了 TrimRight）。冻结的测试由工作流转去修订。
                    # The referee judges the expectation wrong: more implementation edits only bend the code to a wrong test (in a real run the worker added TrimRight to please it). The workflow revises the frozen tests instead.
                    raise ExpectationSuspected('repair cannot change tests: the independent referee judged the test expectation wrong while the actual value stayed the same', verdict)
        if repeated:
            text += chr(10) + 'WARNING: this is the same problem as the previous check, so your last change did not fix it. Do something different: re-read the failing assertion against the acceptance criteria and the examples.'
        # 阻塞性错误（导入/编译/测试准备）会让整个测试运行起不来：问题数看起来很少，其实比几条断言失败糟得多。 Blocking errors (import / compile / test setup) keep the whole test run from starting: few problems on paper, much worse than a few failed assertions.
        blocking = sum(1 for i in own if i['kind'] in ('compile', 'setup'))
        return {'ok': False, 'text': text[:MAX_RESULT_CHARS], 'full': text[:12000], 'signature': sig, 'repeated': repeated, 'problems': len(own), 'blocking': blocking}

    def _streak_ends(self, kind):
        """一次检查通过：连续失败到此为止。签名与逐字消息的历史里放一个 None，“同一个失败连续 N 次”“实际值稳定”都不能跨过一次通过去数。
        A check passed: the failure streak ends. A None goes into the signature and verbatim histories so neither "the same failure N times in a row" nor "the actual value is stable" counts across a pass."""
        if kind != self.failed_kind:  # 通过的是另一种检查（静态通过不能打断测试失败的连续）/ another kind passed (a static pass must not break a streak of test failures)
            return
        self.signatures.append(None)
        self.exact.append(None)
        self.edit_marks.append(self.edits)
        self.failed_kind = None

    def _record(self, problems, blocking=0):
        """修复模式：记住检查结果最好的状态：没有阻塞性错误的优先，再比问题数（同样好取先到的）。工人可能越改越糟，最后交出去的必须是最好的那个状态而不是最后的状态；
        只比问题数会把“导入失败（1 个问题）”误当成比“3 条断言失败”更好。
        Repair mode: remember the best state seen: no blocking errors first, then fewest problems (the earlier one on a tie). A worker can make things worse by editing on; what is handed over must be the best state, not the last one;
        comparing counts alone would rate "an import failure (1 problem)" better than "3 failed assertions"."""
        if not self.repair or problems is None:
            return
        rank = (bool(blocking), problems)
        if self.best is None or self.best_rank is None or rank < self.best_rank:
            self.best_rank = rank
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
                    self._record(check.get('problems') if not check.get('passed') else None, check.get('blocking', 0))
                    if check.get('sealed'):
                        self._record(0)
                    outcome = {**outcome, 'text': (outcome['text'] + chr(10) + 'automatic check after your change: ' + check['text'])[:MAX_RESULT_CHARS + 400], 'full': check.get('full'), 'sealed': check.get('sealed'), 'signature': check.get('signature')}
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
            self._record(outcome.get('problems') if not outcome.get('passed') else None, outcome.get('blocking', 0))
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
                        self._record(extra.get('problems'), extra.get('blocking', 0))
                        return {**extra, 'text': outcome['text'] + chr(10) + extra['text'], 'ok': False}
                    self.passed.add(kind)
                if complete and set(required) <= self.passed:
                    self._record(0)
                if complete and set(required) <= self.passed:  # 所有检查都在当前内容上通过：自动封包，不再依赖模型记得调用 finish / every check passed on the current content: seal automatically instead of relying on the model to call finish
                    return {'ok': True, 'text': outcome['text'] + '. All checks pass and all planned files exist: package sealed.', 'sealed': True}
            return outcome
        if tool == 'recall_step':
            return self.recall_step(action.get('step'))
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
