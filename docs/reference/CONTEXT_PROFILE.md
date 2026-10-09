# 上下文画像（来自 49 次真实运行的账本）

方法：读每次角色调用的输入（账本里的 input_ref），按顶层字段估算 token（项目自己的估算器，中文偏保守），不调用模型。只含默认流程（指挥者/best-of-N 关闭）的运行。

## 每个角色的输入有多大、由什么组成

| 角色 | 调用数 | 平均输入 token | 最大 | 主要组成（占比） |
|---|---|---|---|---|
| project_repair | 61 | 7351 | 9730 | failure_evidence 43%、original_files 33%、checks 10%、spec 6% |
| project_test_revision | 23 | 7459 | 10586 | failure_evidence 43%、original_files 34%、checks 10%、spec 5% |
| project_developer | 109 | 1450 | 2050 | checks 50%、spec 28%、graph 12%、goal 4% |
| project_diagnoser | 43 | 2805 | 3747 | files 49%、failure_evidence 36%、acceptance 6%、goal 2% |
| project_tester | 56 | 512 | 885 | spec 79%、goal 12%、previous_attempt_error 5%、purpose 1% |
| project_planner | 48 | 86 | 115 | goal 73%、purpose 6%、clarification_allowed 2%、previous_attempt_error 1% |
| code_reviewer | 1 | 894 | 894 | files 78%、acceptance 13%、goal 7%、purpose 0% |
| project_conductor | 1 | 545 | 545 | briefing 97%、purpose 1% |

## 输入大小随任务等级的变化（平均 token）

| 等级 | Planner | Tester | Developer | Repair | TestRevision | Diagnoser |
|---|---|---|---|---|---|---|
| L2 | 88 | 488 | 1342 | — | — | — |
| L3 | 86 | 460 | 1432 | 7294 | 7367 | 2894 |
| L4 | 101 | 614 | 1569 | 7606 | 7278 | 2881 |
| L5 | 67 | 466 | 1375 | 7097 | 7885 | 2609 |
| L6 | 99 | 604 | 1588 | 8044 | 6816 | 2945 |
