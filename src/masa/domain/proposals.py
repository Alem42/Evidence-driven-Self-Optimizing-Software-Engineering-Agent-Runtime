"""Pure proposal validation; no transport, storage or execution dependencies."""
import re
from masa.domain.models import MasaError, canonical

def text(value, limit=2000):
    """校验有界非空文本。 Validate bounded nonempty text."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise MasaError('invalid project text')
    return value


def validate_spec(spec):
    """校验可移植目录与标准库 CLI 规格，不相信模型输出。 Validate portable standard-library CLI specifications."""
    if not isinstance(spec, dict) or set(spec) != {'summary','module','entrypoint','files','acceptance'}:
        raise MasaError('invalid ProjectSpec fields')
    text(spec['summary'])
    if not re.fullmatch(r'[a-z][a-z0-9.-]*(/[a-z][a-z0-9_-]*)+', text(spec['module'], 160)):
        raise MasaError('invalid Go module name')
    if spec['entrypoint'] != 'cmd/app/main.go':
        raise MasaError('entrypoint must be cmd/app/main.go')
    files = spec['files']
    if not isinstance(files, list) or not 3 <= len(files) <= 20:
        raise MasaError('project requires 3..20 files')
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
            or name.lower() in names or not (name == 'go.mod' or name.endswith('.go'))):
            raise MasaError('invalid or duplicate project path')
        names.add(name.lower())
    exact_names = {f['path'] for f in files}
    if 'go.mod' not in exact_names or spec['entrypoint'] not in exact_names or not any(n.endswith('_test.go') for n in exact_names):
        raise MasaError('plan requires go.mod, entrypoint and acceptance tests')
    if any(any(other.startswith(n + '/') for other in names) for n in names):
        raise MasaError('file/directory path conflict')
    acceptance = spec['acceptance']
    if not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 24:
        raise MasaError('provide 1..24 acceptance criteria')
    for criterion in acceptance:
        text(criterion)
    if len(canonical(spec).encode('utf-8')) > 40000:
        raise MasaError('project specification exceeds 40 KB')
    return spec


def validate_checks(checks, spec, require_coverage=True):
    """验证 Tester 覆盖验收项；只允许固定工具，不接受命令字符串。 Validate coverage and allowlisted checks, never shell commands."""
    if not isinstance(checks, list) or not 1 <= len(checks) <= 3:
        raise MasaError('Tester must propose 1..3 distinct allowed checks')
    operations = set()
    covered = set()
    for check in checks:
        if not isinstance(check, dict) or not {'operation','purpose','acceptance_indices'} <= set(check) or set(check)-{'operation','purpose','acceptance_indices','cases'}:
            raise MasaError('invalid check fields')
        if not isinstance(check['operation'], str) or check['operation'] not in {'go_test','go_vet','go_fmt_check'} or check['operation'] in operations:
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
    if 'go_test' not in operations:
        raise MasaError('at least one real go_test check is required')
    missing = sorted(set(range(len(spec['acceptance']))) - covered)
    if missing and require_coverage:
        raise MasaError('check plan is missing acceptance criteria: ' + ', '.join(str(i+1) for i in missing))
    if len(canonical(checks).encode('utf-8')) > 16000:
        raise MasaError('check plan exceeds 16 KB')
    return checks


def validate_repair(changes, original):
    """修复只改实现，原测试和模块不变。 Repair implementation only; freeze original tests and module."""
    if not isinstance(changes,dict) or not changes:
        raise MasaError('repair must propose at least one implementation file')
    for path,content in changes.items():
        if path not in original or not path.endswith('.go') or path.endswith('_test.go'):
            raise MasaError('repair cannot change tests, module or file structure')
        if not isinstance(content,str) or not content.strip() or len(content.encode())>60000 or '\x00' in content:
            raise MasaError('invalid repair file content')
    return {**original,**changes}


def validate_files(files, spec):
    """文件集合必须严格匹配批准目录，限制内容与模块声明。 Bind bounded contents to the exact approved file set."""
    validate_spec(spec)
    if not isinstance(files, dict) or set(files) != {f['path'] for f in spec['files']}:
        raise MasaError('files must exactly match the approved project structure')
    total = 0
    for content in files.values():
        if not isinstance(content, str) or not content.strip() or '\x00' in content:
            raise MasaError('invalid generated file content')
        size = len(content.encode('utf-8'))
        if size > 60000:
            raise MasaError('file exceeds 60 KB')
        total += size
    if total > 300000:
        raise MasaError('project exceeds 300 KB')
    if files['go.mod'].strip() != f"module {spec['module']}\n\ngo 1.27.0":
        raise MasaError('go.mod must use the approved module and Go 1.27.0 without dependencies')
    return files

