"""Go 的可行性规则：依据 harness 对 Go 项目的硬约束。 Go feasibility rules: the harness's hard constraints for Go projects.

规则引擎在 application/checks/triage.py（语言无关）；这里只有规则本身。 The engine is language-neutral (application/checks/triage.py); only the rules live here.
"""
INFEASIBLE, RISKY = 'infeasible', 'risky'

# (规则编号, 判定, 关键词正则, 说明, 改写建议)
RULES = [
    ('language', INFEASIBLE,
     r'(?:用|使用|以|in|using|with)\s*(?:python|java(?!script)|javascript|typescript|node(?:\.js)?|rust|c\+\+|c#|php|ruby|swift|kotlin|scala|lua|shell|bash|powershell)\b|'
     r'\b(?:python|javascript|typescript|rust|c\+\+|c#|php|ruby|kotlin|java)\s*(?:实现|编写|写|版本|语言|脚本|程序|命令行|工具|项目|代码|program|script|cli|tool|project|app)',
     '要求的不是 Go。当前选择的语言是 Go，只能生成并验证 Go 项目。',
     '改成 Go 标准库的命令行程序；要用其它语言，请在新任务里选择对应的语言（设置 → 语言 可以看到可用的语言）。'),
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
     r'\bgui\b|图形界面|图形化|窗口程序|桌面(?:应用|程序)|网页|web\s*页面|前端|\breact\b|\bvue\b|小程序|android|ios\s*应用|手机\s*app|摄像头|麦克风|语音识别|opengl|游戏引擎',
     '需要图形界面、网页或硬件。验证只能靠 go test 与命令行输出，无法检查画面或设备。',
     '改成命令行程序，把界面逻辑拆成可测试的纯函数。'),
    ('library_shape', INFEASIBLE,
     r'package\s+(?!main\b)\w+\s*(?:[，,。.;；)）]|$|\s)|实现\s*go\s*函数|只\s*(?:实现|写|需要)\s*函数|不需要\s*(?:命令行|main|入口)|(?:写|实现|提供)一个\s*(?:go\s*)?(?:库|包|sdk|library)\b|保留\s*package',
     '要的是库/函数包（例如 package solution），而 harness 的入口固定为 cmd/app/main.go 的命令行项目（package main）。',
     '改成“命令行程序 + 内部包”：例如“实现命令行 clamp，参数 value min max，核心逻辑放在 internal/clamp 包并测试”。'),
]

# 只给出警告：很可能反复失败，或者验证不了。 Warnings only: likely to need many attempts or hard to verify.
RISKS = [
    # HTML/CSS 只是**输出的文本格式**时（例如 Markdown 转 HTML）完全可测；以前整条按“要图形界面”拦下（VagueBench 的 Markdown 任务因此在零调用时被判不可行）。
    # HTML/CSS as the OUTPUT TEXT format (e.g. Markdown to HTML) is perfectly testable; it used to be blocked as "needs a GUI" (the VagueBench Markdown task was ruled infeasible before any call).
    ('markup_output', r'\bhtml\b|\bcss\b',
     '涉及 HTML/CSS：只能验证生成的文本，无法检查浏览器里的渲染效果。',
     '把验收写成对输出文本的检查（包含哪些标签、属性、转义），不要求“看起来对”。'),
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
