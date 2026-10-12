"""Failure routing based on tool evidence; independent of HTTP.

基于工具证据的失败路由。语言相关的输出解析（哪些诊断算“测试错误”、断言指纹怎么取）由语言包提供；这里只有语言无关的判断。
Routing from tool evidence. Language-specific output parsing (which diagnostics count as test errors, how an assertion is fingerprinted) comes from the language pack; only language-neutral decisions live here.
"""
from masa.languages.registry import DEFAULT_LANGUAGE, registry


def _pack(pack=None):
    return pack or registry.get(DEFAULT_LANGUAGE)


def test_revision_needed(evidence, checks=(), pack=None):
    """按诊断行定位测试错误，避免把实现报错误配给另一行测试。 Match test diagnostics locally, not across unrelated output lines."""
    return _pack(pack).needs_test_revision(evidence, checks)


def repair_advice(checks, pack=None):
    """前端与自动流程复用同一修复分类。 Share evidence-based repair routing with the manual interface."""
    pack = _pack(pack)
    if format_only(checks, pack):
        return {'action': 'format_tests', 'message': '只有 ' + pack.ui().get('format_name', '格式') + ' 格式检查不合格，可直接格式化并重新验证，无需模型调用。'}
    evidence = '\n'.join(str(r.get('stdout', '')) + '\n' + str(r.get('stderr', '')) for _, r in checks)
    if test_revision_needed(evidence, checks, pack):
        return {'action': 'revise_tests', 'message': '检测到测试源码或测试准备错误（如构建目录、常量溢出、导入）。请修订测试并保留行为断言。'}
    return {'action': 'repair', 'message': '未识别到明确的测试准备错误；请结合失败证据检查实现，也可手动选择测试修订。'}


def _format_failure(checks, pack):
    """唯一失败的检查若是格式检查，返回它涉及的路径；否则 None。 The paths of the only failing check when it is the format check, else None."""
    failures = [(op, result) for op, result in checks if result.get('status') != 'completed' or result.get('exit_code') != 0]
    format_operation = pack.op_id('format')
    if len(failures) != 1 or format_operation is None or failures[0][0] != format_operation or failures[0][1].get('status') != 'completed':
        return None
    return [p.strip().replace('\\', '/') for p in failures[0][1].get('stdout', '').splitlines() if p.strip()]


def test_format_only(checks, pack=None):
    """只有测试文件格式不合格时可无模型修订。 Detect the exact deterministic test-format-only case."""
    pack = _pack(pack)
    paths = _format_failure(checks, pack)
    return bool(paths) and all(pack.is_test(p) for p in paths)


def format_only(checks, pack=None):
    """只有格式检查失败，且涉及的文件全是测试或全是实现时，可无模型格式化。
    Only the format check failed, and the files are all tests or all implementation: format deterministically."""
    pack = _pack(pack)
    paths = _format_failure(checks, pack)
    if not paths or not all(pack.is_code(p) for p in paths):
        return False
    tests = [pack.is_test(p) for p in paths]
    return all(tests) or not any(tests)


def repeated_assertion_signature(checks, pack=None):
    """提取失败测试的断言指纹，避免同一矛盾反复消耗修复调用。 / Fingerprint failing assertions to stop repeated ineffective repairs."""
    return _pack(pack).assertion_signature(checks)


def failure_signature(checks, pack=None):
    """失败签名：把所有失败检查的诊断去掉文件名、行列号和数字后取集合。同一问题在多轮修复后仍出现，签名就不变。
    A normalized failure signature: diagnostics stripped of paths, positions and numbers. An unchanged signature after several
    repair rounds means the repairs are not making progress."""
    return _pack(pack).failure_signature(checks)
