# 新增一种语言（语言包开发指南）

目标：为 MASA 新增一种语言（例如 C#、Rust）**不改核心**，只放一个包目录。设计见 [LANGUAGE_PACKS](../design/LANGUAGE_PACKS.md)；Go 与 Python 两个内置包就是样板（`src/masa/languages/go/`、`src/masa/languages/python/`）。

## 1. 包的目录
```
<包目录>/<id>/
  pack.json     清单：id、版本、api_version、能力、布局、操作表、界面提示（声明性）
  pack.py       导出 create(manifest, root) -> LanguagePack 子类的实例（逻辑）
  （其它模块随意：checks、failures、prompts、triage_rules、oracle…）
```
内置包放在 `src/masa/languages/<id>/`；用户包放在「设置 → 语言 → 额外的语言包目录」里配置的目录（或环境变量 `MASA_PACKS_DIR`）。**外部包是在本进程里执行的 Python 代码，只从你明确配置的目录加载。**

## 2. 清单 pack.json（必填项）
| 字段 | 含义 |
|---|---|
| `id` / `title` / `version` / `api_version` | 标识；`api_version` 目前是 1，与核心不兼容的包会被拒绝并在设置页显示原因 |
| `capabilities` | 可选能力：`index`（语法索引）、`single_file_review`、`mutation_review`、`formatter`（有格式检查）。没声明的功能核心会自动关闭 |
| `layout.source_exts` / `test_suffixes` / `manifest_files` / `root_marker` | 源码后缀、测试文件后缀（`is_test` 可覆盖）、清单文件、工作区根必须有的文件 |
| `layout.entrypoint` / `module_regex` / `export_regex` / `subdir_hint` / `min_files` / `max_files` / `template` | 入口文件（可含 `{module}`）、规格里 module 名的正则、契约导出名的正则、布局提示、文件数范围、计划模板名 |
| `operations` | 操作表：`test`（必需）、`static`、`format`、`run`、`index`；每项有账本里的 `id`（全局唯一）、`label`、`exe`（角色名，由 `executables()` 解析）、`args` 模板（可含 `{timeout_ms}`、`{out}`） |
| `ui` | `highlight`、`verify_label`、`format_name` 等给前端的提示 |

操作 id 一旦发布就**不要改**：它们写在账本里（Go 的 `go_test` 就是这个原因）。

## 3. 要实现的成员（LanguagePack）
必需（没有合理默认）：
- **工具链**：`probe()`（就绪与否，写人话）、`fingerprint()`（路径+哈希，写进证据画像）、`executables()`、`env_policy()`（被测代码能看到的环境变量）。
- **规格与文件**：`seed_files(spec)`（由规格确定、不让模型写的文件）、`planning_seed()`、`validate_spec(spec)`、`validate_file_proposal`、`validate_bundle`、`entrypoint(spec)`、`entrypoint_schema()`。
- **确定性检查（零 token）**：`parse_errors()`（语法，带行列号）、`static_messages()`（空转测试、入口规则、未知内部导入、导入的名字不存在…，给模型的重写说明）、`normalize()`（无模型的格式/导入修复）、`declares()`（契约导出）、`outline()`（签名摘要）。
- **失败解析**：`parse_failures()`（原始输出 → 诊断：owner、kind、path、line、message）、`needs_test_revision()`、`assertion_signature()`、`failure_signature()`、`assertion_of()`、`location_regex()`、`relative_path()`、`failure_markers()`。
- **提示词**：`project_prompt()`（Planner/Tester/Developer/Repair/TestRevision 这一族）与 `transport_hint()`（本地模型的补充说明）。
- **规则与判官**：`triage_rules()`（不可行/风险规则；记得包含“要求了别的语言”这一条）、`judge()`（评测的独立判官）。
- **设置**：`config_schema()`（在设置页可调的项，如解释器路径）。

## 4. 通过一致性检查
```
python -m masa lang check <id>
```
它检查布局谓词是否自洽、操作 id 是否唯一且不与别的包冲突、`probe` 能否回答、一个语法错误样本（`samples()`）能否被报出来、提示词能否渲染。**必须全部通过**。测试里 `tests/test_languages.py` 对注册表里的每个包自动参数化运行同一套检查。

## 5. 再做三件事
1. **用脚本化模型 + 真实运行器**写一个端到端测试（见 `tests/test_python_pack.py` 的 EndToEndTests）：规划 → 批准 → 逐文件生成 → 批准 → 真实验证 → Gate。零 token，能发现 90% 的接线错误。
2. **在真实模型上跑几个评测任务**：`python scripts/eval/lang_eval.py --name <名字> --language <id> --only hello,sum,wc --yuan 0.5`（只用 Flash；判官是你写的 `judge()`）。**“Gate 通过但判官不通过”（false_pass）是最有价值的信号**，它几乎总是指向一条缺失的确定性检查（Python 包里的“导入的名字不存在”就是这样加出来的）。
3. 在 `docs/` 记录这个语言的约定与已知局限（例如 Python 没有网络隔离、没有格式检查）。

## 6. Windows 与路径的几个坑（来自 Go、Python 两个包）
- 回溯与编译器输出里的路径可能是绝对路径、带反斜杠：实现 `relative_path()`，并让 `location_regex()` 同时认 `file:line` 与回溯里的 `File "...", line N`。
- 生成的程序在 Windows 上输出 CRLF：提示词要求“比较解码后的文本，不要比较原始字节”。
- 清单文件由模型逐字写对很难：用 `seed_files()` 并让 `override_seeds = True`，以规格为准。
- 空文件（如 Python 的 `__init__.py`）要在 `allow_empty()` 里声明，并放进 `seed_files()`，省掉模型调用。
