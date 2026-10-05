"""冻结之前的测试静态检查：测试文件必须有测试函数、有失败断言、不能 Skip。
Static pre-freeze test checks: a test file must have test functions, failure assertions, and no Skip.

这是“红灯检查”（测试在空实现上必须失败）里**能干净做的那一半**：一个既不断言也不会失败的测试，在任何实现上都是绿的，
等于没有测试。真正的红灯检查需要存根实现或变异（把实现改坏看测试是否变红），放在 M3 的变异门里；这里不假装做到。
This is the half of a "red-light check" (tests must fail against an empty implementation) that can be done cleanly: a test that
never asserts can never fail, so it is green on any implementation, i.e. no test at all. The real red-light check needs a stub
implementation or mutation (break the code, see whether the tests turn red) and belongs to the M3 mutation gate; we do not pretend otherwise.
"""
import re

from masa.application.goimports import _mask

_TEST_FUNC = re.compile(r'^func\s+(?:Test|Example)\w*\s*\(', re.M)
# 任意接收者上的失败调用：t.Fatalf、tt.Errorf、tb.Fail ……；表驱动测试和辅助函数都能覆盖。
# A failure call on any receiver: t.Fatalf, tt.Errorf, tb.Fail ... (covers table-driven tests and helpers).
_FAILURE = re.compile(r'\b\w+\.(?:Fatalf?|Errorf?|FailNow|Fail)\s*\(')
_SKIP = re.compile(r'\b\w+\.(?:Skipf?|SkipNow)\s*\(')
_ASSERT_LIB = re.compile(r'\b(?:assert|require)\.\w+\s*\(')


def test_problems(source):
    """一个 *_test.go 文件的问题列表（空表示没问题）。 Problems of one _test.go file (empty means fine)."""
    code = _mask(source)
    problems = []
    if not _TEST_FUNC.search(code):
        problems.append('the test file declares no Test or Example function, so nothing is tested')
    elif not (_FAILURE.search(code) or _ASSERT_LIB.search(code)):
        problems.append('no test ever fails: add real assertions that call t.Errorf / t.Fatalf when a result differs from the expectation')
    if _SKIP.search(code):
        problems.append('tests must not be skipped (t.Skip hides failures)')
    return problems


def test_problem_messages(files, paths=None):
    """{路径: 说明}。 {path: explanation}."""
    out = {}
    for path in (paths if paths is not None else files):
        text = files.get(path)
        if path.endswith('_test.go') and isinstance(text, str):
            problems = test_problems(text)
            if problems:
                out[path] = '; '.join(problems)
    return out
