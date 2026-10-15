"""Pure proposal validation; no transport, storage or execution dependencies."""
import re
from masa.domain.models import MasaError, canonical

def text(value, limit=2000):
    """校验有界非空文本。 Validate bounded nonempty text."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise MasaError('invalid project text')
    return value


def _pack(language=None):
    """当前语言的包；延迟导入，因为语言包反过来依赖本模块的 MasaError。 The current language's pack; imported lazily because packs depend on this layer too."""
    from masa.languages.registry import registry
    return registry.for_run({'language': language})


def validate_spec(spec, language=None):
    """校验可移植目录与标准库 CLI 规格，不相信模型输出；语言相关的规则来自语言包。 Validate portable standard-library CLI specifications; language rules come from the pack."""
    pack = _pack(language)
    layout = pack.layout
    # examples 是可选字段（Planner 示例，策略 planner_examples）：旧规格没有它，照样合法。 `examples` is optional (the Planner's examples, policy planner_examples): old specs without it stay valid.
    if not isinstance(spec, dict) or set(spec) - {'examples'} != {'summary','module','entrypoint','files','acceptance'}:
        # 说清楚缺哪个、多哪个：模型据此才能在重试时改对（真实运行里三次规划各错一个字段，旧消息只有“字段无效”，规划失败）。
        # Say which field is missing or extra so the model can correct itself on the retry (in a real run three planning attempts each got one field wrong and the old message only said "invalid fields").
        if isinstance(spec, dict):
            missing, extra = {'summary','module','entrypoint','files','acceptance'} - set(spec), set(spec) - {'summary','module','entrypoint','files','acceptance','examples'}
            raise MasaError('invalid ProjectSpec fields' + (f'; missing: {sorted(missing)}' if missing else '') + (f'; not allowed: {sorted(extra)}' if extra else '') + '. The spec has exactly: summary, module, entrypoint, files, acceptance')
        raise MasaError('invalid ProjectSpec fields')
    if 'examples' in spec:
        from masa.domain.examples import validate_examples
        validate_examples(spec['examples'])
    text(spec['summary'])
    if not re.fullmatch(layout['module_regex'], text(spec['module'], 160)):
        raise MasaError(f'invalid {pack.title} module name')
    entry = pack.entrypoint(spec)
    if spec['entrypoint'] != entry:
        raise MasaError(f'entrypoint must be {entry}')
    files = spec['files']
    if not isinstance(files, list) or not layout['min_files'] <= len(files) <= layout['max_files']:
        raise MasaError(f"project requires {layout['min_files']}..{layout['max_files']} files")
    names = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path','purpose'}:
            raise MasaError('invalid planned file')
        name = text(item['path'], 180)
        text(item['purpose'])
        parts = name.split('/')
        if (not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or any(
            p in {'', '.', '..'} or p.startswith('.') or p.endswith('.') or
            re.fullmatch(r'(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])', p.split('.')[0]) for p in parts)
            or name.lower() in names or not (name == pack.root_marker or pack.is_code(name))):
            raise MasaError('invalid or duplicate project path')
        names.add(name.lower())
    # 根目录的源文件会与入口包冲突（Go：“is a program, not an importable package”）；代码必须放在子目录。
    # Root-level source files collide with the entry package (Go: "is a program, not an importable package"); code must live in subdirectories.
    if any('/' not in n and pack.is_code(n) for n in names):
        raise MasaError(f"{pack.title} files must live in subdirectories such as {layout['subdir_hint']}; only {pack.root_marker} may be at the module root")
    exact_names = {f['path'] for f in files}
    if pack.root_marker not in exact_names or spec['entrypoint'] not in exact_names or not any(pack.is_test(n) for n in exact_names):
        raise MasaError(f'plan requires {pack.root_marker}, entrypoint and acceptance tests')
    if any(any(other.startswith(n + '/') for other in names) for n in names):
        raise MasaError('file/directory path conflict')
    acceptance = spec['acceptance']
    if not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 24:
        raise MasaError('provide 1..24 acceptance criteria')
    for criterion in acceptance:
        text(criterion)
    if len(canonical(spec).encode('utf-8')) > 40000:
        raise MasaError('project specification exceeds 40 KB')
    pack.validate_spec(spec)  # 语言包的额外规则（例如 Python 的包必须有 __init__.py）/ extra language rules (e.g. Python packages need __init__.py)
    return spec


CHECK_KEYS = ('operation', 'purpose', 'acceptance_indices', 'cases')
CASE_KEYS = ('name', 'input', 'expected', 'level')
LEVEL_SYNONYMS = {'e2e': 'cli', 'end-to-end': 'cli', 'end_to_end': 'cli', 'system': 'integration', 'functional': 'integration', 'component': 'integration', 'smoke': 'cli', 'regression': 'unit', 'edge': 'unit', 'boundary': 'unit'}


def normalize_checks(checks):
    """把 Tester 的“格式小错”确定性地改正，而不是整次拒绝再付一次费（真实评测里 39 次 Tester 调用有 17 次因格式被拒）：
    · 检查对象多出的字段（最常见的是 `level`）丢掉——它们从不被使用；空的 `cases` 数组丢掉；
    · 同一个操作出现多次（例如分成“单元”和“命令行”两个 py_test）合并成一个：用途拼接、验收索引取并集、用例依次拼接（最多 16 个）；
    · 用例的 `level` 写成同义词（e2e、end-to-end、system…）映射到 unit/integration/cli；用例里多出的字段丢掉。
    不认识的操作、缺字段、索引越界等**实质**错误不动，照样由 validate_checks 拒绝。有效的计划原样返回（不改变任何已有行为）。
    Correct the Tester's formatting slips deterministically instead of rejecting the answer and paying again (in real evaluations 17 of 39 Tester calls were rejected for format reasons):
    extra fields on a check (most often `level`) are dropped because they are never used, and so are empty `cases` arrays; the same operation appearing several times (e.g. a "unit" and a "cli" py_test) is merged into one
    (purposes joined, acceptance indices united, cases concatenated, 16 at most); a case `level` written as a synonym is mapped to unit/integration/cli and extra case fields are dropped. Substantive errors (an unknown
    operation, missing fields, out-of-range indices) are left alone and still rejected by validate_checks. A valid plan is returned unchanged."""
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        return checks
    merged, order = {}, []
    for check in checks:
        if not {'operation', 'purpose', 'acceptance_indices'} <= set(check):
            return checks  # 缺必需字段：这是实质错误，交给校验 / a required field is missing: substantive, left to validation
        item = {k: check[k] for k in CHECK_KEYS if k in check}
        cases = item.get('cases')
        if not cases and 'cases' in item:
            del item['cases']
        elif isinstance(cases, list):
            fixed = []
            for case in cases:
                if isinstance(case, dict) and set(CASE_KEYS) <= set(case):
                    case = {k: case[k] for k in CASE_KEYS}
                    if isinstance(case['level'], str):
                        level = case['level'].strip().lower()
                        case['level'] = LEVEL_SYNONYMS.get(level, level)
                fixed.append(case)
            item['cases'] = fixed
        operation = item['operation']
        if isinstance(operation, str) and operation in merged:
            first = merged[operation]
            first['purpose'] = f"{first['purpose']}\n{item['purpose']}" if isinstance(first['purpose'], str) and isinstance(item['purpose'], str) else first['purpose']
            if isinstance(first['acceptance_indices'], list) and isinstance(item['acceptance_indices'], list):
                first['acceptance_indices'] = sorted(set(first['acceptance_indices']) | set(item['acceptance_indices']), key=lambda i: (not isinstance(i, int), i if isinstance(i, int) else 0))
            if item.get('cases'):
                first['cases'] = (first.get('cases', []) + item['cases'])[:16]
        else:
            if isinstance(operation, str):
                merged[operation] = item
            order.append(item)
    return order


def validate_checks(checks, spec, require_coverage=True, language=None):
    """验证 Tester 覆盖验收项；只允许固定工具，不接受命令字符串。 Validate coverage and allowlisted checks, never shell commands."""
    if not isinstance(checks, list) or not 1 <= len(checks) <= 3:
        raise MasaError('Tester must propose 1..3 distinct allowed checks')
    pack = _pack(language)
    allowed = pack.operation_ids() & {op.id for kind, op in pack.operations.items() if kind in ('test', 'static', 'format')}
    test_op = pack.op_id('test')
    operations = set()
    covered = set()
    for check in checks:
        if not isinstance(check, dict) or not {'operation','purpose','acceptance_indices'} <= set(check) or set(check)-{'operation','purpose','acceptance_indices','cases'}:
            got = sorted(check) if isinstance(check, dict) else type(check).__name__
            raise MasaError(f'invalid check fields: got {got}; each check needs exactly operation, purpose, acceptance_indices and optionally cases')
        if not isinstance(check['operation'], str) or check['operation'] not in allowed or check['operation'] in operations:
            raise MasaError('unauthorized or duplicate check')
        operations.add(check['operation'])
        text(check['purpose'])
        indices = check['acceptance_indices']
        if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(spec['acceptance']) for i in indices):
            raise MasaError('invalid acceptance reference')
        covered.update(indices)
        if 'cases' in check:
            cases=check['cases']
            if not isinstance(cases,list) or not 1<=len(cases)<=16:
                raise MasaError('provide 1..16 concrete test cases')
            for case in cases:
                if not isinstance(case,dict) or set(case)!={'name','input','expected','level'}:
                    raise MasaError('invalid test case contract')
                for field in ('name','expected'):
                    try:
                        text(case[field],1000)
                    except MasaError:
                        raise MasaError('invalid test case ' + field) from None
                # 空输入和空白输入都是有效的边界样例，不能当作缺失说明拒绝。
                # Empty and whitespace fixtures are legitimate boundary inputs, not missing descriptions.
                if not isinstance(case['input'],str) or len(case['input'])>1000 or '\x00' in case['input']:
                    raise MasaError('invalid test case input')
                if case['level'] not in ('unit','integration','cli'):
                    raise MasaError('invalid test level')
    if test_op not in operations:
        raise MasaError(f'at least one real {test_op} check is required')
    missing = sorted(set(range(len(spec['acceptance']))) - covered)
    if missing and require_coverage:
        raise MasaError('check plan is missing acceptance criteria: ' + ', '.join(str(i+1) for i in missing))
    if len(canonical(checks).encode('utf-8')) > 16000:
        raise MasaError('check plan exceeds 16 KB')
    return checks


def validate_repair(changes, original, language=None):
    """修复只改实现，原测试和模块不变。 Repair implementation only; freeze original tests and module."""
    pack = _pack(language)
    if not isinstance(changes,dict) or not changes:
        raise MasaError('repair must propose at least one implementation file')
    for path,content in changes.items():
        if path not in original or not pack.is_source(path):
            raise MasaError('repair cannot change tests, module or file structure')
        if not isinstance(content,str) or not content.strip() or len(content.encode())>60000 or '\x00' in content:
            raise MasaError('invalid repair file content')
    return {**original,**changes}


def validate_test_revision(changes, original, language=None):
    """只允许在显式测试修订中修改既有测试文件。 Allow only existing test files in an explicit test revision."""
    pack = _pack(language)
    if not isinstance(changes, dict) or not changes:
        raise MasaError('test revision must change at least one existing test file')
    for path, content in changes.items():
        if path not in original or not pack.is_test(path):
            raise MasaError(f"test revision may only change existing {pack.layout['test_suffixes'][0]} files")
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > 60000 or '\x00' in content:
            raise MasaError('invalid revised test content')
    return {**original, **changes}


def validate_file_proposal(files, spec, target_path, language=None):
    """逐文件调用只能返回批准的目标源文件。 A file call may return only its approved target source."""
    pack = _pack(language)
    validate_spec(spec, pack.id)
    approved = {item['path'] for item in spec['files']}
    if (not isinstance(target_path, str) or target_path not in approved
            or not pack.is_code(target_path)):
        raise MasaError(f'file generation target must be an approved {pack.title} file')
    if not isinstance(files, dict) or set(files) != {target_path}:
        raise MasaError('file proposal must contain exactly the requested target file')
    content = files[target_path]
    if not isinstance(content, str) or (not content.strip() and not pack.allow_empty(target_path)) or '\x00' in content:
        raise MasaError('invalid generated file content')
    if len(content.encode('utf-8')) > 60000:
        raise MasaError('file exceeds 60 KB')
    pack.validate_file_proposal(files, spec, target_path)
    return files


def validate_files(files, spec, language=None):
    """文件集合必须严格匹配批准目录，限制内容与模块声明。 Bind bounded contents to the exact approved file set."""
    pack = _pack(language)
    validate_spec(spec, pack.id)
    if not isinstance(files, dict) or set(files) != {f['path'] for f in spec['files']}:
        raise MasaError('files must exactly match the approved project structure')
    total = 0
    for path, content in files.items():
        if not isinstance(content, str) or (not content.strip() and not pack.allow_empty(path)) or '\x00' in content:
            raise MasaError('invalid generated file content')
        size = len(content.encode('utf-8'))
        if size > 60000:
            raise MasaError('file exceeds 60 KB')
        total += size
    if total > 300000:
        raise MasaError('project exceeds 300 KB')
    pack.validate_bundle(files, spec)
    return files


def validate_triage(action):
    """本地模型的可行性意见：只能是 ok 或 risky（它没有拦截权）；“infeasible”会被降级为 risky，因为弱模型不能误杀好需求。
    The local model's opinion: ok or risky only (it has no blocking power); "infeasible" is downgraded to risky."""
    if not isinstance(action, dict) or set(action) - {'verdict', 'reasons', 'suggestions'} or action.get('verdict') not in {'ok', 'risky', 'infeasible'}:
        raise MasaError('invalid triage opinion')
    result = {'verdict': 'ok' if action['verdict'] == 'ok' else 'risky'}
    for key in ('reasons', 'suggestions'):
        items = action.get(key, [])
        if not isinstance(items, list) or len(items) > 5 or any(not isinstance(i, str) or len(i) > 400 or '\x00' in i for i in items):
            raise MasaError('invalid triage ' + key)
        result[key] = [i.strip() for i in items if i.strip()]
    if action['verdict'] == 'infeasible':
        result['reasons'] = ['（本地模型认为不可行，已降级为风险提示）'] + result['reasons']
    return result


DIAGNOSIS_OWNERS = ('implementation', 'test', 'both', 'spec', 'unclear')


def validate_diagnosis(action):
    """Diagnoser 的结论：谁的问题 + 依据 + 给修复者的指导。它不能改文件，也不能放宽冻结边界；
    spec/unclear 表示需要人（需求矛盾或证据不足），由工作流确定性地停下。
    The Diagnoser's verdict: whose problem, why, and instructions for the fixer. It edits nothing and cannot relax frozen boundaries;
    spec/unclear mean a human is needed and the workflow stops deterministically."""
    from masa.domain.models import MasaError
    required = {'owner', 'rationale', 'implementation_instructions', 'test_instructions'}
    if not isinstance(action, dict) or not required <= set(action) or set(action) - required - {'expectation_checks'}:
        raise MasaError('invalid diagnosis fields')
    if action['owner'] not in DIAGNOSIS_OWNERS:
        raise MasaError('invalid diagnosis owner')
    out = {'owner': action['owner']}
    for key in ('rationale', 'implementation_instructions', 'test_instructions'):
        value = action[key]
        if not isinstance(value, str) or len(value) > 2000 or '\x00' in value:
            raise MasaError(f'invalid diagnosis {key}')
        out[key] = value.strip()
    out['expectation_checks'] = _expectation_checks(action.get('expectation_checks', []))
    if out['owner'] in ('implementation', 'both') and not out['implementation_instructions']:
        raise MasaError('an implementation diagnosis needs implementation_instructions')
    if out['owner'] in ('test', 'both') and not out['test_instructions']:
        raise MasaError('a test diagnosis needs test_instructions')
    return out


def _expectation_checks(value):
    """Diagnoser 对每个失败用例的独立核对：需求怎么说 / 测试期望什么 / 是否一致。 The Diagnoser's independent per-case check."""
    from masa.domain.models import MasaError
    if not isinstance(value, list) or len(value) > 8:
        raise MasaError('invalid expectation_checks')
    out = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'case', 'requirement_says', 'test_expects', 'matches'} or type(item['matches']) is not bool:
            raise MasaError('invalid expectation check')
        entry = {'matches': item['matches']}
        for key in ('case', 'requirement_says', 'test_expects'):
            if not isinstance(item[key], str) or len(item[key]) > 300 or chr(0) in item[key]:
                raise MasaError('invalid expectation check text')
            entry[key] = item[key].strip()
        out.append(entry)
    return out


def reconcile_diagnosis(diagnosis):
    """让结论服从 Diagnoser 自己的核对结果（确定性）。强模型也会被测试带偏：真实任务里它写出“同额按类别升序”的理由，判成实现问题，
    而它自己对用例的核对如果做了，会发现测试把“按金额降序”写成了升序。规则：核对过的用例全部“测试与需求不一致” ⇒ 测试有问题；
    部分不一致且原判只是实现 ⇒ 两侧都有问题。返回 (新诊断, 是否被改判)。
    Make the verdict obey the Diagnoser's OWN per-case checks (deterministic). Even a strong model can be anchored by the test: in a real task it
    invented a tie-break rule and blamed the implementation, while a per-case check would have shown the test had descending turned into ascending.
    Rule: every checked case disagrees with the requirement => the test is wrong; some disagree and the verdict blamed only the implementation => both."""
    checks = diagnosis.get('expectation_checks') or []
    if not checks or diagnosis['owner'] in ('spec', 'unclear'):
        return diagnosis, False
    wrong = [c for c in checks if not c['matches']]
    if not wrong:
        return diagnosis, False
    lines = chr(10).join(f"- {c['case']}: the requirement says {c['requirement_says']}; the test expects {c['test_expects']}" for c in wrong)
    if len(wrong) == len(checks) and diagnosis['owner'] != 'test':
        return {**diagnosis, 'owner': 'test', 'test_instructions': ('Correct these expectations to follow the requirement:' + chr(10) + lines)[:2000]}, True
    if diagnosis['owner'] == 'implementation':
        return {**diagnosis, 'owner': 'both', 'test_instructions': ('Also correct these expectations to follow the requirement:' + chr(10) + lines)[:2000]}, True
    return diagnosis, False
