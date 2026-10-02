# 近期代码结构与执行流程

2026-10-02。本文解释角色调用、Coordinator、澄清、三种评审与变异评测。最新模型评审为脚本入口，尚未加入前端或自动 Coordinator。

## 目录分工

| 目录/文件 | 功能 |
|---|---|
| application/console.py | 浏览器请求、配置选择、线程生命周期 |
| application/coordinator.py | 自动规划、等待、生成、检查、修复、恢复 |
| application/workflow.py | 原子阶段检查点和重复断言计数 |
| application/planning.py | Planner/Tester 服务、澄清回答、规格批准 |
| application/generation.py | 代码草稿、测试修订、实现修复、快照发布 |
| application/check_policy.py | 根据真实工具诊断分类失败 |
| application/test_review.py | 测试计划规则审查 |
| application/source_review.py | 临时副本上的 Go AST 解析 |
| application/semantic_review.py | 新增独立模型测试计划评审 |
| application/mutation.py | 显式变异、真实测试、结果分类 |
| domain/ | 纯数据协议及校验，不调用模型/工具 |
| runtime/roles.py | 模型请求意图、预算、输出及调用身份持久化 |
| runtime/engine.py | 检查图调度、工具权限与 Evidence Gate |
| infrastructure/ | SQLite、artifact、进程、配置、锁、工作区 |
| runner/internal/indexer | Go AST 的语法事实和空测试诊断 |
| runner/internal/runner | 固定操作、超时取消、进程树控制 |
| frontend/src/features/projects | 项目图、需求回答、方案/代码审查、结果/运行 |

## 一次需求如何流转

HTTP 请求由 Console 启动工作线程。Coordinator 读取 Jobs 检查点，调用 ProjectPlanning。RoleRuntime 在网络请求前保存身份、输入、模型配置和预算；响应后保存结果。Planner 可返回问题，ProjectPlanning 保存 waiting_for_input，线程退出。用户回答事务落盘，再以新的 invocation_id 调用 Planner，旧调用不覆盖。

Tester 提出具体用例，规则审查报告给用户。批准绑定完整规格与检查。Developer 产生文件集合，待审源码可以手动 AST 检查。批准时生成不可变快照，Runtime 执行 go test/vet/格式检查。Gate 核对真实结果。失败时 Coordinator 根据证据选择测试修订或实现修复，新的版本保留 parent_run_id。

恢复读取同一份持久 job，不重新从头生成。completed 调用复用缓存；未知网络结果停止。attempt 是修复轮次，role invocation 的 attempt_no 是同角色调用次数，两者不是一个含义。当前仍是串行有界协调器，没有任意自适应角色 DAG。

## 为什么分开三类评审

规则审查：很便宜，识别明确模式，例如普通随机数必须次次不同。AST 审查：检查实际源码能否解析、测试函数是否确切为空，不能解析类型或证明断言充分。模型语义评审：读取需求、规格、用例之间的意义，发现冲突和遗漏，但仍是建议，不能代替工具或 Gate。

新增 semantic_review.py 创建单独的评审运行，绑定 input_ref。domain/test_review.py 验证每条 finding 的 acceptance/check/case 索引，并要求 evidence 是对应输入中的原文片段。模型不能用不存在的引用支持意见。report 包含 summary、findings 与 needs_attention/reviewed；reviewed 只说明意见记录完成。

评审运行禁止 Runtime 工具执行。它不能修改父项目、批准方案或改变父 Gate。恢复使用 --resume-id，匹配输入和模型配置；结果已保存时不重复请求。脚本为 scripts/review_test_semantics.py。Reviewer 目前可与其他角色使用同一模型，独立的是角色职责和输入，不是独立供应商或训练模型。

```powershell
.\.venv\Scripts\python.exe scripts/review_test_semantics.py d50899063f04459daf4bfdd0a806f85f
```

完整历史计划的真实 API 两次未通过引用校验，当前不能承诺复杂计划评审稳定。不得放宽引用来掩盖问题。后续应采用字段标识+Runtime抽取原文，再逐条审查。

## 变异验证补充了什么

mutation.py 在已成功项目的临时副本中替换实现，测试不变。基线重新通过后，观察错误实现是否失败。killed 表示测试发现此错误，survived 需要人工判断是否等价；无效编译和超时不能算 killed。原 Gate 保持不变。这能测出具体测试能力，比只统计通过率更有价值，但显式小集合不能证明全面正确性。

## 下一阶段

先提高模型引用协议的稳定性并接入可恢复的前端评审入口，再引入 Reviewer→Tester 新提案；不能让 Reviewer 直接删难测试。随后统一角色 Step/Attempt、自动阶段图和测试源码审查，再以固定输入比较上下文 token/质量收益，推进中型跨包任务。
