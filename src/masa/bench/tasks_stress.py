"""压力任务（观察用，不评分）：三个规模和复杂度明显大于 VagueBench 的模糊任务，用来看系统在 15–20 个文件的上限附近先在哪里坏、花多少钱、多少时间。
Stress tasks (observational, not scored): three vague tasks clearly bigger and more complex than VagueBench, to see where the system breaks first near its 15-20 file ceiling, what it costs and how long it takes.

判官只检查“能构建”（没有逐用例的对错标准——题面故意不规定接口）；真正有信息的是账本：规划有没有塞进上限、验证几轮、Gate、花费、时间、卡在哪。
The oracle only checks that the project builds (there is no per-case right answer - the statements deliberately fix no interface); what is informative is the ledger: did the plan fit the limits, how many verification rounds, the Gate, cost, time, where it got stuck.
"""
from masa.bench.tasks import Task


def _builds(args, stdin, out, err, code):
    return True, ''


def _ref(args, stdin):
    return '', 0


STRESS_TASKS = (
    Task('x_minisql', 10, '迷你 SQL 数据库', '写一个命令行迷你 SQL 数据库：支持 CREATE TABLE、INSERT、SELECT（可以带 WHERE、ORDER BY、LIMIT）、UPDATE、DELETE，以及两张表的内连接 JOIN。数据保存在当前目录的一个文件里，每次命令行调用执行一条语句，结果以表格形式打印。',
         (([], ''),), _ref, ('stress', 'state', 'parser'), _builds),
    Task('x_adventure', 10, '文字冒险引擎', '写一个终端文字冒险游戏引擎：从一个 JSON 文件加载世界（房间、物品、门、简单谜题），玩家从标准输入逐行输入 go / take / use / look / inventory 命令；要有存档和读档。',
         (([], ''),), _ref, ('stress', 'state', 'parser'), _builds),
    Task('x_projects', 10, '项目管理工具', '写一个项目管理命令行工具：管理项目、任务、任务之间的依赖、负责人和工时估计；能列出关键路径、每个人的工作量，并导出 Markdown 报告。数据保存在一个文件里。',
         (([], ''),), _ref, ('stress', 'state', 'graph'), _builds),
)
