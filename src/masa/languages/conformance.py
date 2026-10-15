"""语言包一致性检查：每个包都必须满足的最低要求（`masa lang check <id>` 与测试套件共用）。 Conformance checks every language pack must satisfy (shared by `masa lang check <id>` and the test suite).

返回 [(检查名, 通过, 说明)]。需要工具链的检查在工具链未就绪时标为“跳过”（通过，说明里写原因），不假装通过也不误报失败。
Returns [(check name, passed, detail)]. Checks that need the toolchain are skipped (passed, with the reason in the detail) when it is not ready: neither pretending nor failing.
"""
from masa.languages.base import KINDS, LanguagePack
from masa.languages.registry import registry


def check(pack, executor=None):
    results = []

    def add(name, ok, detail=''):
        results.append((name, bool(ok), str(detail)))

    add('is a LanguagePack', isinstance(pack, LanguagePack))
    try:
        entry = pack.entrypoint()
        add('layout: the entrypoint is an implementation file', pack.is_code(entry) and pack.is_source(entry) and not pack.is_test(entry), entry)
    except Exception as exc:  # 一个坏包只报告，不抛出 / a broken pack is reported, never raised
        add('layout: entrypoint', False, f'{type(exc).__name__}: {exc}')
    add('layout: the manifest file is not a source file', not pack.is_source(pack.root_marker), pack.root_marker)
    add('operations: the test operation exists', 'test' in pack.operations)
    add('operations: kinds are known', all(k in KINDS for k in pack.operations), ', '.join(pack.operations))
    ids = [op.id for op in pack.operations.values()]
    add('operations: ids are unique', len(ids) == len(set(ids)), ', '.join(ids))
    clash = sorted(i for i in ids for other in registry.ids() if other != pack.id and i in registry.get(other).operation_ids())
    add('operations: ids do not collide with another pack', not clash, ', '.join(clash))
    try:
        ready = pack.probe(executor)
        add('toolchain: probe answers', True, 'ready' if ready.ok else 'not ready: ' + '; '.join(ready.details))
    except Exception as exc:
        ready = None
        add('toolchain: probe answers', False, f'{type(exc).__name__}: {exc}')
    samples = pack.samples() if hasattr(pack, 'samples') else {}
    broken = samples.get('syntax_error')
    if broken:
        path, source = broken
        if ready is not None and ready.ok:
            found = pack.parse_errors(executor, {path: source}, [path])
            add('checks: a syntax error is reported with its location', bool(found.get(path)), str(found.get(path, ''))[:120])
        else:
            add('checks: a syntax error is reported with its location', True, 'skipped: toolchain not ready')
    else:
        add('checks: samples provided', False, 'the pack gives no samples() with a syntax_error sample')
    add('prompts: the project-role prompt renders', _renders(pack))
    try:
        add('ui: hints are present', isinstance(pack.ui(), dict) and bool(pack.ui().get('highlight', pack.id)))
    except Exception as exc:
        add('ui: hints are present', False, str(exc))
    return results


def _renders(pack):
    try:
        text = pack.project_prompt({'purpose': 'project_developer'}, 'default')
        return isinstance(text, str) and len(text) > 200
    except Exception:
        return False
