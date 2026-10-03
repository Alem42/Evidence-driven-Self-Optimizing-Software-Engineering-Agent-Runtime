# 本地 Planning 修复、硬件与真实模型验证

本轮修复了确定的框架错误，并使用本机 GLM、Qwen 真实推理验证。Qwen 小 CLI 经明确测试修订后通过；GLM 多文件能力仍未稳定。没有调用云端，也没有降低 Gate 或冻结测试边界。

## Planning 根因

用户原任务 `9620c8ba3e5946b3b53ab28666e5cd84` 的 Planner 约14.61秒完成，加载约11.43秒，输出164 tokens，生成约60.64 tokens/s。它提出算法、范围、输出三个问题，正确保存了 waiting_for_input。

旧 model_configuration_frozen 事件 payload 是字符串，Projects.view 假设所有 payload 都是 dict；读取异常让前端无法显示问题。Console.artifact 也有相同假设。既有 stderr 日志为空，不能声称旧日志直接记录了 traceback；结论来自持久账本、代码与回归复现。修复后通过真实 HTTP 读取原任务问题及快照，未代答原需求。

## 代码变化

| 区域 | 改动与作用 |
|---|---|
| runtime/roles.py | 新快照事件对象化；真实传输接收剩余期限；收到完整结果先落盘，取消后不应用；保存失败响应结构诊断 |
| application/projects.py、console.py | 兼容旧标量事件，直接读取快照引用；工作状态区分待答/完成/取消；bootstrap 暴露真实 worker；报告识别项目模型 |
| interfaces/http/server.py | 初始化 Jobs 前独占 console.lock；异常返回 JSON 500 与 request ID，不直接断开连接 |
| infrastructure/diagnostics.py | 有界轮转故障日志，只含类型、帧位置、固定类别；控制页可复制 |
| infrastructure/hardware.py | NVIDIA/CIM 固定只读查询，4秒超时、32KiB限制、10秒缓存，不提权 |
| generation.py、protocol.py、schemas.py、proposals.py | 新本地草稿逐文件持久化；单文件与修复路径约束；CLI 包声明检查；旧草稿/cloud bulk 兼容 |
| application/check_policy.py、coordinator.py | 私有包导入和测试编译器类型错误准确走测试修订；取消/过期不当 Tester 失败重试 |
| React useWorkbench、LiveStatus、OllamaPanel | 独立轮询不丢 job，刷新发现 worker，手动历史不抢回；顶部待答/取消收尾/文件进度；折叠硬件与诊断 |

本轮使用三个审计/实施 Agent 并行检查规划、前端和硬件；项目产品运行时仍是单后台槽，未新增并行 Agent 图。

## 真实案例：成功与失败都保留

**GLM 求和**：新方案 `fc668a9278a54cb2a2f7da4a683fc45b` 经一次真实澄清后完成 Planner/Tester。逐文件生成成功，但生成测试引入了未约定的 helper 行为、私有包导入及语法错误；明确修订仍失败。思考对照调用 `98aa6c2579ad4c7abae8be2f28e8d702` 输出8192 tokens后不完整，实际59.78 tokens/s，未重发。不能据此声称 GLM 多文件闭环稳定。

**Qwen 大小写转换 CLI**：实际安装模型是 qwen3.5:9b-q8_0。早期测试发现错误包名、全局 stdout，以及测试 int/string 类型错误。类型错误被旧策略误路由为实现修复，这是代码问题，已补真实诊断和反例回归。自动流程仍有界停止，未绕过上限。

在失败证据基础上，明确进行测试修订：`eff128c5a2ea4d24b9cd7db9f099b781` 修正类型，但仍有未使用 strings 导入；后续 `93c35b7e3faa4677a0305522947c5057` 仅删除这一导入。已实际对比文件：实现不变，所有测试和断言不变。

最终运行 **dd9ea5f962dc437bb523a34a79c44423**：真实 Go test/vet 与 Gate 通过；通过 harness 再编译、运行五项独立探针：Hello、含空格、显式空参数、无参数、两个参数，分别核对 stdout/stderr/退出码。最后模型修订实际2758输入、674输出 tokens，71.56 tokens/s。证据 `.masa/local-qwen-reviewed-acceptance.json`；代码 `.masa/workspaces/dd9ea5f962dc437bb523a34a79c44423/cmd/app/main.go`。

这是带明确修订方向的真实本地成功，不是完全自动或首轮成功。GLM 原始随机数任务仍保留待答，未擅自推进。默认模型仍为 GLM，Qwen 配置另存可选，不自动换模型或回退 API。安装列表应实时读，测试中一次错误 tag 的404已作为配置失败保留，临时错误配置已删除。

## 验证与当前限制

- Python 全量186项通过；覆盖真实 profile 的快照读取、历史标量、并发取消、剩余期限、第二服务、逐文件中断/复用/未知响应、错误分类。
- 前端13项测试与生产构建通过；Go runner 两个测试包通过。
- 本机硬件查询成功，约0.97秒：RTX 5070 Ti，总显存16303MiB；四条16GiB模块，CIM配置频率3600MHz。七项硬件测试通过；频率不是实时测量。
- 最终真实 HTTP 复验原任务、成功版本、项目列表、硬件、模型目录与诊断均通过，默认仍GLM；不存在的 job 明确404，前端可清除过期跟踪。没有浏览器视觉、点击、小屏或键盘验收。
- Gate只证明选择的检查通过，独立 CLI 探针进一步检查可运行性与行为。模型测试仍可能错误，配置快照还未冻结权重 digest，跨阶段总预算尚未实现。

## 接下来

先提高本地测试质量、语法预检和明确失败停止，减少无效修复；之后补接口摘要、上下文准入、digest 与总预算，再做模型升级。详细解释、结构和模型切换方式见 [代码讲解](../guides/USER_LOCAL_RUNTIME_AND_RECOVERY.md)。状态已收敛到 STATUS/PLAN_NEXT_STAGE/CURRENT，旧 progress 只作为历史。

已按增量提交：994b951、4e3186f、e827ed8、72c89b3；后续收敛提交见 Git 历史。运行数据库、密钥和生成代码均留在忽略的本地目录。
