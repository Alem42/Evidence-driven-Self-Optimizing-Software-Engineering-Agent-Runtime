"""包间接口契约（多包项目）：确定性的部分——什么时候需要、怎么校验、生成后怎么核对“承诺的导出都写出来了”。契约本身由 `package_contract` 角色（数据角色）一次调用产出。
Package interface contract (multi-package projects): the deterministic parts: when one is needed, how it is validated and how to check after generation that "every promised export was written". The contract itself comes from one call of the `package_contract` data role.

起因（第一次 10 文件项目的真实运行，bank）：各包的测试和实现分开生成，包与包之间的接口和“错误优先级”只存在于自由文本的验收项里，首轮就出现两个包对同一规则理解不同。
契约把每个包的导出签名和跨包规则写成结构化的东西，放进已批准的规格旁边，Tester、Developer、Repair 都能看到同一份。
Origin (the first 10-file real run, bank): tests and implementations of the packages are generated separately, and the interfaces between packages and the error-precedence rules only exist as free text in the acceptance items, so
round 1 already had two packages disagreeing about one rule. The contract writes each package's exported signatures and the cross-package rules as structure next to the approved spec, visible to the Tester, Developer and Repair alike.

小项目不受影响：只有规格里至少有 3 个内部包（internal/ 下的目录）时才需要契约。 Small projects are unaffected: a contract is only wanted when the spec has at least three internal packages.
"""
import re

from masa.domain.models import MasaError

MIN_PACKAGES = 3
_EXPORTED = re.compile(r'^[A-Z][A-Za-z0-9_]*$')


def internal_dirs(spec):
    return sorted({i['path'].rsplit('/', 1)[0] for i in spec['files'] if i['path'].endswith('.go') and i['path'].startswith('internal/') and '/' in i['path']})


def wanted(spec):
    """规格里有足够多的内部包时才需要契约。 A contract is wanted only when the spec has enough internal packages."""
    return len(internal_dirs(spec)) >= MIN_PACKAGES


def validate_contract(raw, spec):
    """校验角色给出的契约：目录必须是规格里的内部包，导出名必须是导出标识符，数量有上限；不合法抛 MasaError（调用方忽略契约，规划照常）。
    Validate the contract the role gave: directories must be internal packages of the spec, names exported identifiers, bounded counts; illegal raises MasaError (the caller ignores the contract and planning goes on)."""
    dirs = set(internal_dirs(spec))
    if not isinstance(raw, dict) or not isinstance(raw.get('packages'), list) or not 1 <= len(raw['packages']) <= 16:
        raise MasaError('invalid package contract')
    seen, packages = set(), []
    for entry in raw['packages']:
        directory = entry.get('dir') if isinstance(entry, dict) else None
        if directory not in dirs or directory in seen:
            raise MasaError(f'contract names an unknown or repeated package {directory!r}')
        seen.add(directory)
        exports = entry.get('exports')
        if not isinstance(exports, list) or not 1 <= len(exports) <= 12:
            raise MasaError(f'contract for {directory} needs 1..12 exports')
        clean = []
        for item in exports:
            if not isinstance(item, dict) or not _EXPORTED.match(str(item.get('name', ''))) or not isinstance(item.get('signature'), str) or not 1 <= len(item['signature']) <= 240:
                raise MasaError(f'contract for {directory} has an invalid export')
            clean.append({'name': item['name'], 'signature': item['signature'].strip()})
        packages.append({'dir': directory, 'exports': clean})
    rules = raw.get('rules', [])
    if not isinstance(rules, list) or len(rules) > 12 or any(not isinstance(r, str) or not 1 <= len(r) <= 300 for r in rules):
        raise MasaError('contract rules must be at most 12 short strings')
    return {'packages': packages, 'rules': rules}


def contract_problem_messages(files, paths, contract, planned_paths):
    """{路径: 说明}。某个包的**最后一个**计划内的非测试 Go 文件刚生成完时，核对契约承诺的导出是否都已声明；缺的就地重写时点名。
    {path: explanation}. When the LAST planned non-test Go file of a package has just been written, check that every export the contract promises is declared; missing ones are named in the rewrite."""
    if not contract:
        return {}
    out = {}
    promised = {p['dir']: p['exports'] for p in contract['packages']}
    for path in paths:
        if not path.endswith('.go') or path.endswith('_test.go') or '/' not in path:
            continue
        directory = path.rsplit('/', 1)[0]
        if directory not in promised:
            continue
        siblings = [p for p in planned_paths if p.endswith('.go') and not p.endswith('_test.go') and p.rsplit('/', 1)[0] == directory]
        if not siblings or siblings[-1] != path:
            continue
        source = '\n'.join(str(files.get(p, '')) for p in siblings)
        missing = [e for e in promised[directory] if not re.search(r'(?m)^(?:func(?:\s*\([^)]*\))?\s+%s\b|type\s+%s\b|var\s+%s\b|const\s+%s\b|\t%s\b)' % ((re.escape(e['name']),) * 5), source)]
        if missing:
            out[path] = (f'package {directory} must declare what the package contract promises, still missing: ' + '; '.join(f"{e['name']} — {e['signature']}" for e in missing[:6])
                         + '. Other packages and tests rely on exactly these names and signatures.')
    return out
