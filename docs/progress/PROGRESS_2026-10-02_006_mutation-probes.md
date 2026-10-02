# 最小变异评测与源码审查验证

2026-10-02。

## 本轮实现

application/mutation.py 提供显式小规模评测；scripts/evaluate_mutations.py 是可复用入口。必须选择已有 Gate 成功的生成项目，最多五个变异。变异只替换已批准实现文件中的一个确切片段，测试文件和 go.mod 不允许修改。先重新验证基线，再逐个临时副本运行同一测试；请求、输入、结果和报告均保存。每次调用最多六个受控 Go 测试请求，是独立评测预算，不改原 Run 工具预算或 Gate。

| 结果 | 含义 |
|---|---|
| killed | 可运行变异被测试发现，返回非零 |
| survived | 该变异下测试仍通过；需要判断是否等价/符合规格 |
| invalid | 构建/准备失败，不计作测试发现业务错误 |
| inconclusive | 超时、取消或截断，无法判断 |

这不是自动变异引擎或测试正确性证明。当前集合手工指定，没有自动从 AST 生成变异，也没有模型语义评审或前端变异入口。

## 真实随机数案例

复用成功项目 d50899063f04459daf4bfdd0a806f85f，原测试基线再次通过。真实 Go runner 结果：

- 相等边界强制返回 0：killed。
- 反转 min > max 校验：killed。
- 随机种子 1,2 改为 3,4：survived，符合当前不规定种子的契约，不能直接算测试缺口。
- 语法破坏：invalid，未被误算成 killed。

本轮没有新 LLM 调用。这是此前真实 LLM 生成/修复项目上的实际工具评测，原快照与 succeeded Gate 保持不变。

## 复现

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_mutations.py d50899063f04459daf4bfdd0a806f85f tests/fixtures/random-mutations.json
```

该集合绑定此项目的具体实现片段；换项目需要提供自己的 JSON。报告保存于 artifact，源运行事件 mutation_evaluation_report 引用 report_ref。脚本会打印报告及各变异结果引用。

## 源码入口补齐

HTTP 测试使用真实 Go parser，确认待审状态保持、报告可重载、三次解析后拒绝超预算。前端重载只显示匹配当前保存文件集合的报告；编辑后清除旧结果。新增工具预算回归。取消场景仍待补齐，浏览器点击验收仍未完成。

## 验证和下一步

完整 133 Python 测试通过；随后新增 HTTP 测试所在20项全部通过。前端4项与构建通过。下轮优先独立模型语义评审，输出绑定规格的 findings；再扩大变异到退出码与整数边界，推进上下文收益对照和中型仓库。不要把 survived 自动视为失败，或把语法破坏当作有效杀死。
