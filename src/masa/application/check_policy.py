"""Failure routing based on tool evidence; independent of HTTP."""
import json
import re

def test_revision_needed(evidence, checks=()):
    """按诊断行定位测试错误，避免把实现报错误配给另一行测试。 Match test diagnostics locally, not across unrelated output lines."""
    lines=[]
    for line in evidence.splitlines():
        try:
            frame=json.loads(line)
            output=frame.get('Output','') if isinstance(frame,dict) else line
        except (ValueError,TypeError):
            output=line
        lines.extend(str(output).splitlines())
    if 'import cycle not allowed in test' in evidence:return True
    markers=('imported and not used','declared and not used','undefined:',
             'syntax error','missing return','overflows int','executable file not found in %PATH%')
    if any(re.search(r'_test\.go:\d+:',line) and
           (any(marker in line for marker in markers) or re.search(r'_test\.go:\d+:\d+: expected ',line))
           for line in lines):return True
    # 测试内构建选错目录属于测试准备错误；不能让 Developer 修改冻结测试。
    # Building an empty package from a test is a test-setup failure, not an implementation repair.
    if any('no Go files in ' in line for line in lines) and any(re.search(r'_test\.go:\d+:',line) for line in lines):
        return True
    for operation,result in checks:
        if operation!='go_fmt_check' or result.get('exit_code')==0:continue
        paths=[p.strip().replace('\\','/') for p in result.get('stdout','').splitlines() if p.strip()]
        if paths and all(p.endswith('_test.go') for p in paths):return True
    return False


def repair_advice(checks):
    """前端与自动流程复用同一修复分类。 Share evidence-based repair routing with the manual interface."""
    if test_format_only(checks):
        return {'action':'format_tests','message':'只有测试文件格式不合格，可直接格式化并重新验证，无需模型调用。'}
    evidence='\n'.join(str(r.get('stdout',''))+'\n'+str(r.get('stderr','')) for _,r in checks)
    if test_revision_needed(evidence,checks):
        return {'action':'revise_tests','message':'检测到测试源码或测试准备错误（如构建目录、常量溢出、导入）。请修订测试并保留行为断言。'}
    return {'action':'repair','message':'未识别到明确的测试准备错误；请结合失败证据检查实现，也可手动选择测试修订。'}


def test_format_only(checks):
    """只有测试文件格式不合格时可无模型修订。 Detect the exact deterministic test-format-only case."""
    failures=[(op,result) for op,result in checks if result.get('status')!='completed' or result.get('exit_code')!=0]
    if len(failures)!=1 or failures[0][0]!='go_fmt_check':return False
    result=failures[0][1]
    if result.get('status')!='completed':return False
    paths=[p.strip().replace('\\','/') for p in result.get('stdout','').splitlines() if p.strip()]
    return bool(paths) and all(p.endswith('_test.go') for p in paths)


def repeated_assertion_signature(checks):
    """提取失败测试的断言指纹，避免同一矛盾反复消耗修复调用。 / Fingerprint failing assertions to stop repeated ineffective repairs."""
    for operation, result in checks:
        if operation != 'go_test' or result.get('exit_code') == 0:
            continue
        assertions = []
        for line in result.get('stdout', '').splitlines():
            try:
                frame = json.loads(line)
                output = frame.get('Output', '') if isinstance(frame, dict) else line
            except (ValueError, TypeError):
                output = line
            for item in str(output).splitlines():
                if re.search(r'_test\.go:\d+:', item) and re.search(r'\b(?:want|expected|got)\b', item, re.I):
                    assertions.append(re.sub(r'_test\.go:\d+:', '_test.go:', item.strip()))
        if assertions:
            return tuple(sorted(set(assertions)))
    return ()


