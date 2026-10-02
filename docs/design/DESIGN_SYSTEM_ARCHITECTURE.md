# 当前最小架构

2026-10-03。

```text
React 工作台 → HTTP / Console → Coordinator
                                  ├─ Planning / Generation / Review
                                  ├─ RoleRuntime → LLM
                                  └─ Tool Runtime → Go Runner → Gate
                  Store：Run / 调用 / artifact / 事件 / Job 检查点
```

目录保留 domain、application、runtime、agents、intelligence、infrastructure、interfaces。没有新插件总线、重复项目状态表或任意动态图。

| 组件 | 负责什么 | 状态依据 |
|---|---|---|
| Coordinator | 串行推进、等待、恢复、最多四轮修复 | workflow_jobs 持久检查点 |
| Planning / Generation | 校验角色提案、审批、发布新版本 | project_plan 与 artifact |
| RoleRuntime | 模型调用意图、结果、预算与复用 | role_invocations；旧表只迁移一次 |
| Tool Runtime | 白名单检查调度、快照核对 | steps / attempts / tool_calls |
| Gate | 独立核对真实工具结果 | 真实退出码、检查覆盖与证据 |
| Projects | 只读展示投影 | 上述事实和事件，不新增业务状态 |

run_kind 区分规划、代码草稿、验证和语义评审；旧记录读取时推导。规划和评审即使终态也不能被当成工具验证。角色完成响应不等于业务校验通过，审批也不等于工具通过。

UI 主状态由当前版本来源链投影：模型请求/完成/失败对应角色，等待输入对应澄清，awaiting_review 对应审批，step 状态对应 Executor/Gate。线程存活单独决定是否显示运行进度和恢复按钮。调用/工具表是事实，不以 UI 投影回写它们。

前端历史侧栏、中央任务/代码/验证/日志标签、右侧角色时间线、底部状态条；新建需求表单只在开始项目时显示。检查图渲染已删除，后端检查图仍负责依赖调度与 Gate 覆盖。角色流程是状态投影，不是任意 Agent 图。workspaceState仅解释状态，useWorkbench区分跟随执行与手动历史浏览。

保留隔离副本、批准引用、冻结测试、工具预算、不确定调用不重放。隔离副本不是完整安全沙箱；当前只支持标准库 Go CLI。旧单文件与只读 demo 仅兼容保留，退出主产品路线。模型路由与持续对话见愿景文档，尚未实现。
