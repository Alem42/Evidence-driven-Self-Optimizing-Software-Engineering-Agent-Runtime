"""独立判官：把系统生成的项目真的构建并运行，用任务的参考实现逐用例比对。
Independent oracle: really build and run the generated project and compare every case with the task's reference implementation.

它与被测系统无关：不读系统的测试、不信系统的 Gate。系统说“通过”而判官说“不对”，就是“假通过”——这是对“证据驱动”最直接的检验。
It is independent of the system under test: it ignores the system's tests and Gate. A Gate pass that the oracle rejects is a "false pass",
the most direct test of the "evidence-driven" claim.
"""
from pathlib import Path


def normalize(text):
    """逐行去掉行尾空白并去掉末尾空行：宽容格式噪声，不宽容内容。 Strip trailing whitespace per line and trailing blank lines: tolerant of noise, not of content."""
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def judge(files, task, executor=None, language=None):
    """按语言分派到语言包的判官。executor 可以是工具链路径（旧用法，当作默认语言的工具链）或执行器对象。
    Dispatch to the language pack's oracle. `executor` may be a toolchain path (the legacy form, taken as the default language's toolchain) or an executor object."""
    import types
    from masa.languages.registry import registry
    if isinstance(executor, (str, Path)):
        executor = types.SimpleNamespace(go_executable=Path(executor))
    return registry.for_run({'language': language}).judge(files, task, executor)
