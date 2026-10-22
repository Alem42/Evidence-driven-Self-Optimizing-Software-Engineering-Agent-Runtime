"""Python 的变异体生成：对实现文件做**单点**的小改动（比较符、加减、与或、常数 ±1、去掉 not/strip/lower、反转 reverse），用来衡量一套测试“够不够强”。
Mutant generation for Python: ONE small change at a time (comparison, add/sub, and/or, constant +-1, dropping not/strip/lower, flipping reverse) to measure how strong a test suite is.

为什么 / Why (ROADMAP E9, P1-3 d): Gate 的强度就是测试的强度（EVIDENCE E17/E24/E28）。变异分数 = 被测试杀死的变异体 / 全部变异体——给“测试够不够强”一个客观数字；
测试通过而判官不通过的存活变异体，就是一个“假通过”的真实样本。
The Gate is exactly as strong as the tests. The mutation score (killed / all) makes "are the tests strong enough" a number, and a mutant that survives the tests but is rejected by an independent oracle is a REAL sample of a false pass.

纯函数，零 token，不执行任何代码。 Pure functions, zero tokens, nothing is executed here.
"""
import ast
import copy
import random

SWAPS = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
ARITH = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult, ast.Mod: ast.Mult}
DROPPABLE = {'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'casefold'}


def _sites(tree):
    """所有可以被改动的位置：(种类, 节点路径下标)。 Every place that can be changed."""
    sites = []
    for index, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Compare) and any(type(op) in SWAPS for op in node.ops):
            sites.append(('compare', index))
        elif isinstance(node, ast.BinOp) and type(node.op) in ARITH:
            sites.append(('arith', index))
        elif isinstance(node, ast.BoolOp):
            sites.append(('boolop', index))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
            sites.append(('unary', index))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            sites.append(('bool', index))
        elif isinstance(node, ast.Constant) and isinstance(node.value, int):
            sites.append(('int', index))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in DROPPABLE and not node.args:
            sites.append(('drop_call', index))
        elif isinstance(node, ast.If):
            sites.append(('negate_if', index))
    return sites


def _apply(tree, kind, index):
    nodes = list(ast.walk(tree))
    node = nodes[index]
    description = f'{kind}@{getattr(node, "lineno", "?")}'
    if kind == 'compare':
        position = next(i for i, op in enumerate(node.ops) if type(op) in SWAPS)
        node.ops[position] = SWAPS[type(node.ops[position])]()
    elif kind == 'arith':
        node.op = ARITH[type(node.op)]()
    elif kind == 'boolop':
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == 'unary':
        parent = next(p for p in nodes if any(child is node for child in ast.iter_child_nodes(p)))
        _replace(parent, node, node.operand)
    elif kind == 'bool':
        node.value = not node.value
    elif kind == 'int':
        node.value = node.value + 1 if node.value != 0 else 1
    elif kind == 'drop_call':
        parent = next(p for p in nodes if any(child is node for child in ast.iter_child_nodes(p)))
        _replace(parent, node, node.func.value)
    elif kind == 'negate_if':
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
    return description


def _replace(parent, old, new):
    for field, value in ast.iter_fields(parent):
        if value is old:
            setattr(parent, field, new)
            return
        if isinstance(value, list):
            for i, item in enumerate(value):
                if item is old:
                    value[i] = new
                    return


def generate(source, limit=12, seed=0):
    """[(描述, 变异后的源码)]：从所有位置里按种子均匀抽样 limit 个；源码无法解析返回空。 Sample `limit` mutants uniformly with the seed; unparseable source gives none."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    sites = _sites(tree)
    random.Random(seed).shuffle(sites)
    out = []
    for kind, index in sites:
        if len(out) >= limit:
            break
        mutant = copy.deepcopy(tree)
        try:
            description = _apply(mutant, kind, index)
            ast.fix_missing_locations(mutant)
            text = ast.unparse(mutant)
        except (StopIteration, ValueError, TypeError):
            continue
        if text.strip() != ast.unparse(tree).strip():
            out.append((description, text))
    return out
