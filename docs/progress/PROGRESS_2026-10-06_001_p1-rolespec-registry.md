# 进展018：P1 RoleSpec 注册表

2026-10-06。分支 `feature/dynamic-roles`（从 `feature/multi-model` 切出）。设计与验收见 [DESIGN_DYNAMIC_ROLES](../design/DYNAMIC_ROLES.md) §2.1、§3 的 P1。

## 1. 做了什么

**角色成为数据，注册表是角色元数据的唯一来源。** 之前角色信息散在 7 处：提示词 if 链、输出 schema、校验器、路由的角色表、默认的“直接用最高等级”列表、模型配置的角色白名单、前端标签与顺序。现在：

| 位置 | 之前 | 现在 |
|---|---|---|
| `src/masa/roles/specs/<id>.json`（+ 可选 `<id>.prompt.md`） | — | 一个角色一份元数据（id、标签、描述、order、level、routable、权限、校验器名、提示词、输出 schema、max_output_tokens、when、tools、input） |
| `roles/registry.py` | — | 加载**并在加载时校验**（id 格式、与文件名一致、未知字段（防拼写错误）、描述 ≤120 字、权限写范围、prompt 与 prompt_builder 二选一、提示词文件存在、schema 是对象……）；`use(dir)` 供测试临时换注册表 |
| `roles/jsonschema.py` | — | 零依赖的最小 JSON-Schema 校验器（type/enum/const/properties/required/additionalProperties/items/min·max/oneOf） |
| `agents/protocol.py` | `if purpose == …` 长链 | `instruction_for` 与 `validate_response` 按注册表分发；`PROMPT_BUILDERS`、`VALIDATORS` 两张表；新增通用校验器 `json_schema`；`check_registry()` 在导入时检查每个角色引用的校验器/构造器存在 |
| `agents/schemas.py` | 诊断、预检的 schema 写在代码里 | 这两个迁入各自的 JSON；注册表里有 `output_schema` 就直接用（约束解码） |
| `application/routing.py` | 硬编码 `ROLES` 与 `prefer_highest_roles` | 都由注册表派生；`validate_policy` 每次向注册表要最新的角色集合 |
| `infrastructure/llm.py` | 硬编码角色白名单 | `registry.current().config_roles()` |
| `/api/roles`、前端 | 静态标签与顺序 | 新增只读端点（不含提示词全文）；前端 `useRoles()` 把名字写进标签表，`ModelsStrip` 的顺序、路由页的角色列表都来自它；静态标签保留为兜底 |
| `pyproject.toml` | — | 声明 package-data，保证 JSON/MD 被打进包 |

## 2. 怎么保证“行为逐字节不变”

迁移**之前**先抓了黄金样本（`tests/golden/`：15 个上下文覆盖每个角色的每个提示词分支，含 Planner 有/无澄清、Developer 整包/逐文件、测试审查两种协议、历史角色、未注册 purpose、无 purpose；记录 `instruction_for` 与 `response_schema` 的结果）。迁移后测试逐字节比对，**连 schema 的属性顺序也比**（约束解码按顺序生成，诊断的“先核对、后下结论”依赖它）。提示词与 schema 是用脚本从**现有代码的实际输出**生成成文件的，没有手抄。
我还验证了黄金样本真能抓偏差：临时把一份提示词里的一个单词改大小写，测试立即失败，还原后通过。

## 3. 验收

| 标准（来自设计文档） | 结果 |
|---|---|
| 现有后端/前端测试**不改任何断言**即全绿 | ✅ 迁移完成后跑全量：404 项通过，**没有改动任何既有测试** |
| 注册表校验测试：缺字段、未注册 validator、重复 id、未知字段、坏 id、文件名不符、提示词文件缺失……都在**加载时**报错 | ✅ 14 类畸形定义逐一断言 |
| 新增角色**只加 JSON + MD，不改 Python**，即被路由器与 `llm.validate_config` 接受 | ✅ `DataOnlyRoleTests`：路由策略、模型配置、提示词、输出契约、通用校验（含先脱敏再校验）、未知角色仍被拒绝、已有角色渲染不受影响 |

测试：后端 **423**（新增 19）、前端 **33**（新增 2）。

## 4. 诚实说明：P1 没有做什么

- **提示词不是全部都在文件里。** Planner/Tester/Developer/Repair/TestRevision 共用一套随上下文分支的提示词（整包/逐文件、有无澄清、本地覆盖），测试审查也随协议版本变化，所以它们登记为 `prompt_builder`（注册表里指向 `protocol.py` 的构造器函数），**不是**静态文件。静态提示词（诊断、预检、历史角色）已经成了 `.prompt.md`。把这几个“随上下文变化”的提示词也改成模板文件，需要先设计占位符机制，收益有限、风险较高，**没有在 P1 做**。
- **新角色目前只能用 `json_schema` 通用校验器**（或引用已有的命名校验器）。要写业务级校验仍要在 `VALIDATORS` 里加一个 Python 函数——这符合“数据驱动”的目标范围，但不是“任意校验逻辑都能用数据表达”。
- `when`、`tools`、`input`、`tunable` 字段已经被**加载和校验**，但 P1 里还没有任何代码**使用**它们——它们是留给 P2（指挥者的“可选角色清单”与简报）和 P3（调优器）的。
- 前端“路由页的角色列表”现在包含诊断角色（之前是 5 个，现在是注册表里全部 `routable` 的 6 个）；这是界面上的小变化，后端策略本来就支持给诊断设置起步等级。
- 没有跑任何真实模型：P1 是纯重构，验证靠黄金样本和全量测试，**没有花云端 token，也没用显卡**。

## 5. 下一步
P2（指挥者）。入口：`flow.py` 增加 `llm_choice` 节点类型；`coordinator.py` 的简报生成；`project_conductor` 作为第一个**完全由数据定义**的角色（它同时是对“数据驱动角色”的真实检验）。开关默认关闭。
