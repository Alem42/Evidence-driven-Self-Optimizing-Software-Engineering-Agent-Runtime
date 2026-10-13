# 提示排布：让服务端的前缀缓存起作用

> 一句话：发给模型的用户消息里，**整个任务都不变的内容放前面、每一步都在变的内容放后面**，让 DeepSeek 一类按“请求开头的最长公共前缀”做的上下文缓存能命中更多。只改序列化顺序，不改内容，也不改账本里的输入身份。

## 为什么

DeepSeek 的上下文缓存按**请求开头的相同前缀**匹配，命中部分按更低的价格计费并且更快；用量里会给出 `prompt_cache_hit_tokens / prompt_cache_miss_tokens`。本地的 Ollama/llama.cpp 也会复用相同前缀的 KV。

以前上下文按键名字母序序列化。`last_results`、`step`（V2 工人每一步都变）夹在 `acceptance`、`goal`、`notes` 之间，前缀在第一个变化的字段处就断了。

## 做法

- [`src/masa/domain/prompt_layout.py`](../../src/masa/domain/prompt_layout.py)：一张从“最稳定”到“最易变”的字段表（`ORDER`），表里没有的字段排在中间（`*`）、同档字母序；嵌套值仍是字母序。
- 排序依据是真实账本里每个字段在**同一角色连续两次调用之间变化的比例**（253 份账本、4 841 次角色调用）：

| 角色 | 几乎不变（排前） | 偶尔变 | 每次都变（排后） |
|---|---|---|---|
| package_worker | purpose、goal、language、acceptance、examples（0%） | package、own_files、interfaces、notes（4–6%）、files（40%） | step、last_results、previous_attempt_error（100%） |
| project_developer | purpose、contract、graph、language（0–2%）、goal/spec/checks（10%） | — | previous_attempt_error（63%）、target_path、previous_files（90%） |
| project_repair | purpose、graph、language、goal/spec/checks（2–16%） | original_files（62%） | failure_evidence（63%）、feedback（73%）、context_selection（80%） |

- 不变量（有测试）：键与值一个不少；`digest(context)`（字母序）不变，所以账本里的输入、已有调用的“输入身份”都不受影响；`MASA_PROMPT_LAYOUT=sorted` 回到旧排布；追加的用户提示（`user_notes`）排在会变的那一档里，不破坏稳定前缀。
- 检视器（追踪面板里点任意一次调用）会显示：各部分占多少 token、与同角色上一次调用共享的开头占多少、服务端报告的命中率。

## 量化

**1. 零 token 回放**（`python scripts/eval/prefix_replay.py`）：在 4 841 次真实调用上，假设服务端按 64 token 一块缓存“与同角色更早调用的最长公共前缀”：

| | 字母序（旧） | 稳定内容在前（新） |
|---|---|---|
| 合计可缓存前缀 | 55.9% | **64.1%** |
| package_worker | 73.5% | 81.2% |
| project_developer | 56.3% | 65.2% |
| project_repair | 17.3% | 29.9% |

这是理论估计，不是服务端实测。

**2. 真实对照**（Go `wordfreq`，V2，只用 Flash；`scripts/eval/ab_eval.py --arms v2,v2s`，三轮交替先后；数据 `.masa/cache_ab/summary.json`）：

| 排布 | 服务端报告的缓存命中率 | 花费 | 调用数 | 判官 |
|---|---|---|---|---|
| 新（稳定在前） | 65% · 69% · 69%（均值约 68%） | ¥0.026 · 0.035 · 0.030 | 6 · 6 · 6 | 3/3 通过 |
| 旧（字母序） | 44% · 36% · 38%（均值约 39%） | ¥0.028 · 0.053 · 0.066 | 6 · 12 · 14 | 3/3 通过 |

怎么读：**同一个任务、同一批调用里，新排布的命中率明显更高**（第一对两边都是 6 次调用：65% vs 44%）。旧排布那组有两次因为验证失败多跑了一轮（调用更多、更贵），这是模型的随机性，**不能归因于排布**，所以只拿命中率说话，不拿花费说话。样本是单一小任务、3 对，只作方向；命中部分的价格折扣没有计入我们的花费估算（估算按未命中价，是上界）。

## 复现

```bash
python scripts/eval/prefix_replay.py                  # 零 token
python scripts/eval/ab_eval.py --name cache_ab --language go --only wordfreq --arms v2,v2s --models flash --start-level 2 --yuan 0.6 --per-run 0.15 --round 1
```

`v2s` 臂 = 同样的 V2，只是 `MASA_PROMPT_LAYOUT=sorted`。
