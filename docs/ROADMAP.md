# 路线图（重新判断后的版本）

2026-10-06。前置阅读：[PROJECT_OVERVIEW](PROJECT_OVERVIEW.md)。本文取代 PLAN_MULTI_MODEL_RUNTIME §8–§10 与 PLAN_LOW_MODEL_QUALITY §7 里的优先级（那两份保留为历史与细节参考）。
本文的做法与岗位要求来自公开资料，文末列了来源；论文细节凭记忆概括，对外引用前请核对原文。

## 1. 这个项目应该是什么（定位）

先承认一个事实：**目前简历里的很多词（Evidence Gate、付费调用永不重放、测试先行……）是这个项目自己的叫法**，读的人无从判断它们是不是重要。更好的办法是把项目放进业界已有的名字里：

> **一个“可验证、可恢复、成本可控”的编码 Agent Runtime。**
> 对应业界三个已经被认可的方向：
> ① **执行验证的 Agent**（SWE-agent / OpenHands / Aider：改代码→跑测试→看结果→再改）；
> ② **Agent 评测与可靠性工程**（SWE-bench 式的独立评测 harness、golden dataset、可观测）；
> ③ **LLM 网关与成本控制**（LiteLLM / RouteLLM / FrugalGPT：多模型路由、预算、降级、级联）。
> 外加一个基础设施主题：**durable execution（持久执行）**——长任务崩溃后怎么续跑、怎么不重复花钱（Temporal 解决的问题）。

这个定位的好处：面试官不需要理解“我们的 Gate”，只需要知道“这是一个带评测和成本路由的 SWE-agent 类系统”，然后你再讲细节。

## 2. 主流 Agent 开发岗位在要什么

综合检索到的岗位描述与技能指南（来源见文末）：

| 主流要求 | 出现的形式 |
|---|---|
| Python + 异步并发 | “async 是必须的”；流式、结构化输出、function/tool calling |
| **多模型与 token 经济学** | 生产团队按成本/延迟/能力在 Anthropic、OpenAI、Bedrock 之间路由 |
| **图/状态机编排框架** | LangGraph 或同类；能讲清它们各自的优点和在生产里的失败模式 |
| **多智能体：supervisor / worker** | “监督者—工人”模式；子 Agent 超时、跨多次工具调用保持状态 |
| **评测** | “设计评测套件，包括 golden dataset，**验证 Agent 的输出而不是信任它**” |
| **可观测与韧性** | 非确定性系统必须做超时、重试、降级、追踪 |
| RAG / 向量库 / 提示工程 | 几乎每个岗位都有 |
| 工程交付 | Git、CI/CD、容器化、测试 |

## 3. 对照：你的两个项目各覆盖了什么

| 能力 | Policy RAG 项目 | 本项目（MASA/Verdict） | 缺口 |
|---|---|---|---|
| LangGraph 编排、工具调用、ReAct | ✅ | 自研声明式工作流（等价于条件边的图） | 本项目没用 LangGraph，需要能讲清“为什么自研”（见 §3.2）并给出适配 |
| RAG / 混合检索 / 重排 | ✅ | — | 不需要重复 |
| MCP | ✅ | ❌ | 低成本补：把验证/评测/路由暴露为 MCP 工具 |
| Checkpointer / interrupt-resume | ✅（框架提供） | ✅ 自研，且做到**付费调用不重放** | 讲清和 LangGraph 的差别 |
| **多模型路由、预算、级联** | ❌ | ✅ | **这是本项目对 RAG 项目最大的互补** |
| **执行验证 / 沙箱执行** | ❌ | ✅（Go runner、Job Object） | 非 Go 语言 |
| **评测（带独立判官）** | 检索评测（Recall@K、MRR） | ✅ 23 题 10 级 + 假通过率 | 公开可复现的报告 |
| 流式、取消、stall | SSE 流式问答 | ✅ 真取消+stall | — |
| 可观测 | run_id 事件记录 | 账本（事件溯源） | **缺标准出口：OpenTelemetry / Langfuse** |
| 多智能体 supervisor/worker | ReAct 双路径 | 固定角色（Planner/Tester/Developer/Diagnoser） | **动态角色（W6）** |
| 部署（Docker/CI） | ✅ | ❌ | 补 Dockerfile + CI |

结论：**本项目补的正是 RAG 项目没有的“成本路由 + 执行验证 + 评测 + 持久执行”四块**；它缺的是“与主流生态的接口”（MCP/OTel/LangGraph）和“动态多智能体”。

### 3.1 这个项目独特在哪（诚实版）
不是任何单个零件独特——路由有 LiteLLM，持久执行有 Temporal，评测有 SWE-bench，子 Agent 有 Claude Code。**独特之处在于把它们在一个小系统里做成了一个闭环，并且用自己的评测证明了哪些有用、哪些没用：**
1. **“验证”被当成一等公民**：成功由账本推导而非模型宣布；并用独立判官度量验证本身的可靠度（发现 Gate 放行 ~17% 的错误，并追到根因）。多数开源 Agent 只有前半（跑测试），没有“对验证的验证”。
2. **持久执行细到“付费 LLM 调用”这一粒度**：LangGraph 的 checkpoint 保存状态，但不保证崩溃后自动续跑、也不区分“付费调用结果未知”该不该重放；Temporal 解决了前者但不理解 LLM 调用的计费语义。这里把“免费本地调用可重试、付费调用永不盲重放”做成了账本级规则，并用真实 `kill -9` 演练验证。
3. **诚实的负结果**：评测显示“本地起步”目前不如纯云端（57% vs 79%，云端 token 持平）——大多数项目只展示好看的数字，能量化地说“这个设计目前没带来收益，原因是……”本身就是工程能力的证据。
4. 不独特的要承认：声明式工作流、级联路由、测试先行都是已知做法（见 §5、§6 的出处）。

### 3.2 为什么自研而不是用 LangGraph（决策记录，面试高频问题）
- 我要的核心保证是：**崩溃后不重复付费、不丢证据**。LangGraph 的 checkpointer 保存的是“数据”，不是“执行”：进程死了，需要有人检测失败、决定从哪一步重入、重启它；Temporal 官方也是这样区分的，并提供了 LangGraph 插件把它跑在持久执行引擎上。我需要把这个语义细化到“单次付费调用”，所以先自己做出来，理解它到底要保证什么。
- 代价：自研要自己维护；收益：能清楚讲出框架没替你解决的那一部分。
- **计划**：保留内核，加一个 **LangGraph 适配**（把 fix-v1 导出成 LangGraph 图、把账本映射为 checkpointer），证明语义可以落到主流框架上，也方便面试时对照。

## 4. 路线重新判断：三条备选与取舍

| 路线 | 内容 | 对求职的价值 | 风险 | 判断 |
|---|---|---|---|---|
| **A. 继续加运行时功能**（W2 GPU 调度、W3 多样本、W4 逐函数、W5 Worker、W7 经验卡） | 把 M2 做完 | 深，但读的人难看懂；多数功能解决的是“本地小模型不够强”这个本项目特有的问题 | 实测显示本地路径尚未带来收益，继续堆功能是在没验证价值的方向上加码 | ❌ 不再作为主线 |
| **B. 让项目“可读、可对接、可讲清”**（本文 §6 的 P0–P2） | README+一键演示、MCP/OTel/LangGraph 适配、动态角色最小实现、Gate 强化 | **最高**：直接对齐岗位关键词，面试官一眼看懂 | 需要克制“再多做点功能”的冲动 | ✅ **主线** |
| **C. 转成通用 Agent 平台**（多语言、多任务类型） | 加 Python/pytest、非编码任务 | 通用性好 | 战线拉长、深度被稀释 | ⚠️ 只做“加 Python 语言包”这一步（见 P4），不做通用化 |

**判断：走 B，辅以 C 的最小一步，A 里只挑能提升“证据质量”和“可对接性”的（Gate 强化、Worker 租约）。**
理由：① 你做这个项目的目的是补 RAG 项目在岗位上缺的部分——缺的是“工程化能力 + 评测 + 成本路由 + 生态接口”，而不是更多功能；② 当前最大的“故事风险”是读不懂，不是功能不够；③ 实测的负结果说明继续在本地模型上加码回报不确定。

## 5. 动态角色：有哪些实现路线，选哪条，为什么

先明确“动态角色”指什么。现在的角色（Planner/Tester/Developer/Repair/Diagnoser）写死在代码里，工作流只能在固定角色间切换。“动态”可以有四个层次：**选哪个角色（运行时选择）**、**角色长什么样（提示词/工具/权限是数据）**、**要不要新增角色（运行时生成）**、**整个流程图怎么连（运行时生成）**。

### 5.1 六条路线

| # | 路线 | 做法 | 代表 | 优点 | 缺点 |
|---|---|---|---|---|---|
| A | 固定角色 + 条件路由 | 角色在代码里，图的边按条件选择下一个 | CrewAI、MetaGPT、LangGraph 条件边 | 简单、确定、好测 | 增加角色要改代码 |
| B | **Handoff（把交接做成工具）** | 每个 Agent 把“转交给 X”暴露成一个函数工具，模型调用它即切换 | **OpenAI Agents SDK / Swarm**：模型看到 `transfer_to_<agent>`，调用后运行时复制上下文、初始化目标 Agent 的指令并继续 | 模型决定下一步，扩展方便 | 依赖模型的路由判断；弱模型容易乱转 |
| C | **Orchestrator–Workers** | 一个“指挥”模型运行时把任务拆成子任务，派给专门的 worker，观察结果后决定下一步 | **Anthropic《Building effective agents》** 的 orchestrator-workers；LangGraph supervisor；Anthropic 多 Agent 研究系统 | 适合“子任务无法预先确定”的问题 | 指挥模型必须够强；调用多、贵 |
| D | **角色即配置（声明式注册表）** | 角色 = 一份数据：名称、描述、系统提示词、可用工具、权限；运行时按需实例化 | **Claude Code 的 subagent**：`.md` 文件 + YAML frontmatter（name/description/tools）+ 提示词正文，独立上下文窗口；AutoGen Studio 的 JSON | 新增角色不改代码；可版本化、可审计；可导入导出 | 角色仍需人写 |
| E | **运行时让 LLM 生成角色** | LLM 根据任务现写角色（提示词+工具）并实例化 | **AutoAgents**（IJCAI 2024，按任务自动生成并协调多个 Agent，另设 observer 反思）、AOrchestra（2026，自动创建子 Agent） | 最灵活，无需预设 | 质量不稳；难验证；贵；小模型做不到 |
| F | **自动搜索工作流/Agent 设计** | 用 LLM 或搜索算法在评测集上迭代生成并选择最优的工作流 | **AFlow**（LLM 生成并更新工作流、自动生成角色）、ADAS | 能找到人想不到的流程 | 需要大量评测与算力；偏研究 |

Anthropic 的文章还有一条**根本性建议**：先区分 *workflow*（流程由代码预先写定）和 *agent*（由模型决定下一步），**能用 workflow 就不要上 agent**，因为后者更不可预测、更贵；只有当问题结构无法预先确定时才用 orchestrator-workers。

### 5.2 对本项目最合适的是什么：**D + 受限的 B（再用评测把关，取 F 的“评估”这一半）**

**推荐方案：“角色即数据（RoleSpec 注册表）→ 由确定性 guard 选择 → 可选地让模型在已注册的角色里提议下一个 → 全部经校验器与预算 → 用自带评测决定要不要采用动态提议。”**

理由，逐条对应本项目的约束：
1. **本项目的核心卖点是“可验证、可恢复”，动态必须不破坏它。** 路线 E、F（运行时生成角色/流程）产出的东西没有预先验证过，也无法保证恢复语义；路线 D 的角色是**数据**，可校验、可版本化、可进账本。
2. **本项目大量用小模型。** E、F、C 都要求“指挥/生成”的模型足够强；小模型靠不住。只让模型在**已注册的有限集合**里选（B 的受限版）比让它自由发挥可靠得多；而且已经有 `prefer_highest_roles`，指挥类决策本来就交给最强模型。
3. **已有的工作流引擎正好承接。** `fix-v1` 已经是“节点 + 已注册 guard + 校验器 + 步数上限”，动态选择 = 增加由 RoleSpec 注册的节点和 guard，不需要重写引擎。
4. **成本与预算已有现成通道。** 每个角色的等级、可用工具、token 上限都写进 RoleSpec，路由器照常按预算决策；E/F 的“多调用试探”会冲击预算模型。
5. **有评测可以回答“动态是否更好”。** F 的精髓不是“生成”，而是“用评测选”。我们没有算力去搜索，但可以把“固定图 vs 允许模型提议”当作两个条件，在评测集上比较——这正是自建评测的用武之地。
6. **生态兼容。** RoleSpec 的字段刻意与 Claude Code subagent（name、description、tools、prompt）和 OpenAI Agents SDK（instructions、tools、handoffs）对齐，可以**导入/导出**，面试时能说“我的角色定义和主流格式互通”。

**为什么不选其它：**
- A（纯固定）：就是现状，不满足“动态”。
- C（orchestrator-workers）：适合开放式研究类任务；我们的任务结构（规划→测试→实现→验证→修复）本来就是固定 workflow，Anthropic 的建议恰恰是这种情况不要上 agent。可以把它留给“未来更开放的任务”。
- E、F：研究价值高，但对小模型不可靠、成本高、难以保证恢复与审计；作为 related work 写进文档、不实现。

### 5.3 分阶段（每一步都可独立交付和验证）
| 阶段 | 内容 | 验收 |
|---|---|---|
| **W6a RoleSpec 注册表** | 把 6 个角色的元数据（提示词、输出 schema、校验器、默认等级、可用工具/权限）收敛成数据；路由、前端、`llm.py` 的角色白名单都从注册表读 | 新增一个角色只改数据不改 if 链；现有 400+ 测试全绿 |
| **W6b 角色选择由 guard 驱动** | 可选角色（如 `code_reviewer`、`test_skeptic`）作为注册节点，由确定性 guard（失败签名、轮数、归属）触发 | 评测里“触发的机制计数”可见；开关可控 |
| **W6c 受限提议（handoff 式）** | 最强模型可以在“已注册、当前 guard 允许”的角色中提议下一个；提议经校验器（白名单、预算、步数、不能越过 Gate）后才执行，否则回退到确定性路径 | 非法提议被拒绝并留痕（有测试） |
| **W6d 评测决定是否启用** | 同一套任务分别跑“固定图”与“允许提议”，比较稳定等级、假通过率、每次通过的 token | 数据证明有收益才默认启用；没收益就保持关闭并记录结论（负结果也是产出） |
| **W6e 互通** | RoleSpec ⇄ Claude Code subagent markdown、OpenAI handoff 工具描述 的导入导出 | 能导入一份现成的 subagent 文件并运行 |

## 6. 其它后续优化（做什么 · 主流怎么做 · 为什么 · 优先级）

| 优先级 | 项 | 做什么 | 主流做法与理由 |
|---|---|---|---|
| **P0** | **可读性**：README + 90 秒演示 | 一页讲清“解决什么问题 + 怎么跑一个例子 + 看到什么”；一条命令跑金丝雀并展示结果；架构图；术语表（已写在 PROJECT_OVERVIEW） | 面试官打开仓库的前 90 秒决定印象。再强的内部设计，读不懂就等于没有 |
| **P1** | **MCP 服务** | 把 `verify_project`、`run_benchmark`、`route_preview`、`get_run_report` 暴露为 MCP 工具（FastMCP stdio） | MCP 已是工具接入的事实标准；RAG 项目已有 MCP，复用经验，成本低；让别的 Agent 能调用“我的验证和评测” |
| **P1** | **OpenTelemetry / Langfuse 导出** | 账本事件 → OTel span（一次任务一个 trace，每次模型/工具调用一个 span，带 token、成本、模型、是否重试）；可选推送到 Langfuse | 岗位要求“可观测”；账本本来就是事件溯源，映射是机械的；这是最便宜的“对齐主流”的一步 |
| **P1** | **LangGraph 适配** | 把 fix-v1 导出成 LangGraph 的 StateGraph，把账本接成 checkpointer，跑同一批评测任务对照 | 证明“自研内核的语义能落到主流框架上”，直接回答“为什么不用 LangGraph” |
| **P2** | **动态角色 W6a–e** | 见 §5 | 对齐“多智能体/supervisor-worker”要求，且不牺牲可验证性 |
| **P2** | **Gate 强化（M3）** | ① Gate 自己把产物用 Tester 的 CLI 用例跑一遍（需要 Tester 给精确期望 stdout）；② 追溯矩阵：每条验收项至少对应一个测试用例；③ 变异测试（把实现改坏看测试是否变红）作为“测试的测试” | SWE-bench 式 harness 强调“测试对模型隐藏、环境干净”；变异测试是评估测试集质量的经典做法。直接针对实测的 17% 假通过 |
| **P3** | **Worker 租约与自动续跑（W5）** | API 与 Worker 拆分，命令表+租约，崩溃后自动接管 | 这是 Temporal 的核心能力；写一个对照文档“LangGraph checkpointer / Temporal / 本项目”，比再实现一遍更有价值；如果实现，保持小而精 |
| **P3** | **Python 语言包** | 增加 pytest 验证的任务与 runner 适配，评测加 Python 题 | 多数 Agent 岗位以 Python 为主，能降低“只会 Go”的印象；这是 C 路线里唯一值得做的一步 |
| **P4** | 多样本取优（W3）、逐函数生成（W4） | 本地免费算力多采样、隔离副本并行验证取优 | AlphaCode/CodeT/Agentless 的做法；它能解释“为什么混合模式有可能反超纯云端”，但**先做 P0–P2**，因为目前没有证据表明这是瓶颈 |
| **P4** | 经验卡（W7） | 修复成功后沉淀规则 | Voyager 技能库思路；价值要靠评测验证，先只记录 |
| **不做** | 通用平台化、GPU 调度（W2）、运行时生成角色/流程 | — | 前两者战线过长且用户特有；后者见 §5.2 |

### 6.1 关于“本地起步不如纯云端”该怎么办（诚实的技术判断）
实测：混合 57% / 云端 token 基本持平 / 慢 1.75 倍。原因有三，对应三类优化：
1. 本地小模型一次通过率低，修复轮次多，并且每次升级都把更长的上下文重新发给云端（上下文重复成本）。→ 优化：**升级时只带“摘要 + 最小证据”，不带全部历史**；这是级联路由的常见陷阱——级联会在困难样例上把延迟翻倍，必须靠“更准的起步等级判断”弥补。
2. 简单任务上云端一次生成已经很便宜，本地的相对优势不明显。→ 优化：**按任务难度决定起步等级**（RouteLLM 的思路：训练/规则判断“这个请求该用强模型还是弱模型”），而不是所有任务都从本地起步。
3. 本地模型算力受限只能部署小模型，是客观约束。→ 如果硬件不变，价值主张应调整为“**可控、可审计的混合**”而非“降本”，并把重点放在评测与可靠性上。
这也是路线 B 的又一个理由：与其在本地路径上无限加码，不如把“怎么判断该不该用本地”做成可评测的路由策略。

## 7. 建议的执行顺序（按性价比）

1. **P0（1–2 天）**：README + 一键演示；简历按新版本改写。
2. **P1（3–5 天）**：MCP 服务 → OTel/Langfuse 导出 → LangGraph 适配（每个都小）。
3. **P2（约 1 周）**：W6a–c（RoleSpec、guard 选择、受限提议）；Gate 强化的第①②项。
4. **评测收口**：W6d、LangGraph 对照、Gate 强化前后各跑一次标准套餐，写一份可复现的评测报告（这是简历数字的真正来源）。
5. P3/P4 视时间。

## 来源
- 岗位技能：[How to Become an AI Agent Engineer in 2026](https://skillscouter.com/how-to-become-an-ai-agent-engineer/)、[The Complete AI Agent Engineer Skills Stack You Need in 2026](https://www.mygreatlearning.com/blog/the-complete-ai-agent-engineer-skills-stack-you-need-in-2026/)、[How to Land AI Agent Jobs in 2026](https://dev.to/mhmalvi/how-to-land-ai-agent-jobs-in-2026-1jbg)、[Sr. AI/LangGraph Engineer（职位）](https://www.dice.com/job-detail/0171eeae-cfaf-43cd-b52c-6670bd0c6e2e)
- 动态角色：[Anthropic — Building Effective Agents（Spring AI 文档转述）](https://docs.spring.io/spring-ai/reference/api/effective-agents.html)、[Simon Willison 对该文的总结](https://simonwillison.net/2024/Dec/20/building-effective-agents)、[Claude Code subagents](https://code.claude.com/docs/en/sub-agents)、[OpenAI Agents SDK handoffs](https://www.promptlayer.com/glossary/openai-agents-sdk)、[AutoAgents（IJCAI 2024）](https://www.ijcai.org/proceedings/2024/3)、[AOrchestra](https://arxiv.org/html/2602.03786v1)、[Flow: Modularized Agentic Workflow Automation](https://arxiv.org/pdf/2501.07834)
- 评测 harness：[OpenAI — Introducing SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/)、[SWE-bench 说明](https://qaskills.sh/blog/swe-bench-explained-guide-2026)
- 路由与持久执行：[5 Open-Source Tools to Control Your AI API Costs](https://www.finout.io/blog/5-open-source-tools-to-control-your-ai-api-costs-at-the-code-level)、[LLM routing in production](https://tianpan.co/blog/2025/10/19/llm-routing-production)、[Temporal 的 LangGraph 插件（持久执行）](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)
