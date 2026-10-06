"""可行性预检（triage）：在花任何模型调用之前，判断一个需求是否“确定会失败”或“风险很高”。
Feasibility triage: before spending any model call, judge whether a requirement is CERTAIN to fail or very risky.

两层 / Two layers:
  1. 确定性规则（本文件）：只依据 harness 的硬约束——标准库 Go 命令行项目、入口 cmd/app/main.go、隔离副本、无网络、
     无第三方依赖、用 `go test` 验证。命中“不可行”规则的需求无论模型多强都过不了，所以直接拦下并给出可操作的改写建议。
     Deterministic rules from the harness's HARD constraints. A requirement that hits an infeasible rule cannot pass with any
     model, so it is stopped early with an actionable rewrite hint.
  2. 本地模型复核（application/triage_model.py，可选）：只许把判断升级为“有风险”，不许拦截——弱模型不能误杀好需求，
     而且只用本地模型，不花 API 费用。
     Optional local-model review: it may only raise a warning, never block, and never uses a paid API.

规则很保守：宁可放行也不误杀（命中且未被否定词抵消才算）。Rules are conservative: when in doubt they let the task through.
"""
import re

OK, RISKY, INFEASIBLE = 'ok', 'risky', 'infeasible'

# 否定词：出现在关键词前面的短窗口内，则视为“明确不要它”。 Negators within a short window before a keyword cancel the hit.
_NEGATORS = ('无', '不', '不要', '不使用', '不需要', '没有', '禁止', '避免', '除了', 'without', 'no ', 'not ', "don't", 'never', 'avoid', 'except')

# (规则编号, 判定, 关键词正则, 说明, 改写建议)
RULES = [
    ('language', INFEASIBLE,
     r'(?:用|使用|以|in|using|with)\s*(?:python|java(?!script)|javascript|typescript|node(?:\.js)?|rust|c\+\+|c#|php|ruby|swift|kotlin|scala|lua|shell|bash|powershell)\b|'
     r'\b(?:python|javascript|typescript|rust|c\+\+|c#|php|ruby|kotlin)\s*(?:实现|编写|写|版本|语言|program|script)',
     '要求的不是 Go。目前 harness 只能生成并验证 Go 项目（Go 工具链是唯一的检查工具）。',
     '改成 Go 标准库的命令行程序；其它语言要等语言包（M2 之后）。'),
    ('third_party', INFEASIBLE,
     r'\b(?:gin|echo|fiber|cobra|viper|gorm|sqlx|logrus|zap|testify|urfave/cli|goquery|colly|chi|mux)\b|github\.com/|第三方(?:库|包|依赖|框架)|外部(?:库|依赖)|go\s*get\b|go\.sum',
     '用到了第三方依赖。检查在隔离副本里运行，没有网络、不安装依赖，go.mod 也被固定为无依赖。',
     '只用 Go 标准库实现（flag、encoding/json、sort、strings 等）。'),
    ('network_service', INFEASIBLE,
     r'(?:连接|访问|调用|请求|查询)\s*(?:远程|外部|互联网|在线|线上)|爬虫|crawler|scrap(?:e|ing)|下载(?:文件|网页|页面)|websocket|grpc|mysql|postgres|postgresql|mongodb|redis|kafka|数据库(?:连接|服务)|'
     r'\bhttp\.(?:get|post)\b|call(?:s|ing)?\s+(?:a|an|the)?\s*(?:remote|external)\s*api',
     '需要网络或外部服务（数据库、远程 API、爬虫）。检查环境没有网络，无法验证。',
     '把外部输入换成命令行参数或本地文件，把外部服务抽象成接口并用内存实现。'),
    ('gui', INFEASIBLE,
     r'\bgui\b|图形界面|图形化|窗口程序|桌面(?:应用|程序)|网页|web\s*页面|前端|\bhtml\b|\bcss\b|\breact\b|\bvue\b|小程序|android|ios\s*应用|手机\s*app|摄像头|麦克风|语音识别|opengl|游戏引擎',
     '需要图形界面、网页或硬件。验证只能靠 go test 与命令行输出，无法检查画面或设备。',
     '改成命令行程序，把界面逻辑拆成可测试的纯函数。'),
    ('library_shape', INFEASIBLE,
     r'package\s+(?!main\b)\w+\s*(?:[，,。.;；)）]|$|\s)|实现\s*go\s*函数|只\s*(?:实现|写|需要)\s*函数|不需要\s*(?:命令行|main|入口)|(?:写|实现|提供)一个\s*(?:go\s*)?(?:库|包|sdk|library)\b|保留\s*package',
     '要的是库/函数包（例如 package solution），而 harness 的入口固定为 cmd/app/main.go 的命令行项目（package main）。',
     '改成“命令行程序 + 内部包”：例如“实现命令行 clamp，参数 value min max，核心逻辑放在 internal/clamp 包并测试”。'),
]

# 只给出警告：很可能反复失败，或者验证不了。 Warnings only: likely to need many attempts or hard to verify.
RISKS = [
    ('interactive', r'交互式|实时|键盘(?:输入|控制|事件)|终端(?:界面|动画)|\btui\b|倒计时|动画|游戏',
     '涉及交互/实时/终端界面：自动测试只能通过标准输入输出验证，容易出现“能跑但测不到”。',
     '把游戏/界面逻辑做成纯函数（状态 → 新状态），用测试覆盖；主程序只做输入输出。'),
    ('concurrency', r'并发|goroutine|协程|定时(?:器|任务)|超时|心跳|channel|race',
     '涉及并发或时间：测试容易不稳定（时序、竞态）。',
     '把并发部分隔离成可注入时钟/通道的小函数，测试里用确定性输入。'),
    ('performance', r'性能|基准|benchmark|高并发|吞吐|毫秒级|大规模数据|百万级',
     '涉及性能指标：go test 默认不验证性能，Gate 无法判断是否达标。',
     '把性能要求改成可验证的复杂度约束或小规模样例。'),
    ('large_scope', r'完整的?(?:系统|平台|项目)|大型|微服务|多模块|管理系统|电商|社交|erp|crm',
     '范围很大：一次生成多个子系统，成功率低、修复成本高。',
     '先做最小可验证的一块（一个命令 + 一个核心包），再逐步追加。'),
    ('unverifiable', r'好看|美观|优雅|用户体验|友好|人性化|智能|最优解|尽可能快',
     '包含无法用测试判定的主观要求。',
     '把主观要求换成可检查的具体行为（输出格式、退出码、边界值）。'),
]


def _negated(text: str, start: int) -> bool:
    """关键词前面的短窗口里有否定词吗？ Is the keyword negated in the short window before it?"""
    window = text[max(0, start - 12):start].lower()
    return any(n in window for n in _NEGATORS)


def _hits(text: str, pattern: str) -> list[str]:
    found = []
    for m in re.finditer(pattern, text, re.I):
        if not _negated(text, m.start()):
            found.append(m.group(0).strip()[:40])
    return found


def assess(goal: str) -> dict:
    """纯函数：返回 {verdict, findings:[{rule, level, matched, reason, suggestion}], summary}。无模型、无 I/O。
    Pure function: no model, no I/O."""
    text = goal or ''
    findings = []
    for rule, level, pattern, reason, suggestion in RULES:
        hit = _hits(text, pattern)
        if hit:
            findings.append({'rule': rule, 'level': level, 'matched': hit[:3], 'reason': reason, 'suggestion': suggestion})
    for rule, pattern, reason, suggestion in RISKS:
        hit = _hits(text, pattern)
        if hit:
            findings.append({'rule': rule, 'level': RISKY, 'matched': hit[:3], 'reason': reason, 'suggestion': suggestion})
    if len(text) > 4000:
        findings.append({'rule': 'long_goal', 'level': RISKY, 'matched': [], 'reason': f'需求很长（{len(text)} 字符），模型容易漏掉细节。',
                         'suggestion': '拆成更小的任务，先做核心行为。'})
    if len(text.strip()) < 6:
        findings.append({'rule': 'too_short', 'level': RISKY, 'matched': [], 'reason': '需求太短，Planner 需要猜测大量细节。',
                         'suggestion': '写清输入、输出和错误处理。'})
    verdict = INFEASIBLE if any(f['level'] == INFEASIBLE for f in findings) else RISKY if findings else OK
    names = {INFEASIBLE: '确定会失败', RISKY: '有风险', OK: '未发现问题'}
    return {'verdict': verdict, 'findings': findings, 'summary': names[verdict]}
