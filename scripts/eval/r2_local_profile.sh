#!/usr/bin/env bash
# R2 全本地能力档案的驱动：逐个（模型, 任务, 轮次）调用 ab_eval，便于中途插队（存在 .masa/r2.pause 时在两次运行之间等待）和续跑（ab_eval 跳过已完成的）。
# Driver of the R2 all-local capability profile: one ab_eval call per (model, task, round), so other GPU work can cut in between runs
# (wait while .masa/r2.pause exists) and the whole thing resumes (ab_eval skips what is done). Ends with FINISHED-<code> in the log.
# usage: bash scripts/eval/r2_local_profile.sh "<model>|<name>|<round>|<scale> ..." >> .masa/r2.log 2>&1   (model names contain ':', hence '|')
cd "$(dirname "$0")/../.." || exit 1
# MASA_STATE：状态目录（在 git worktree 里跑时指向主仓库的 .masa，源设置也从那里取）/ the state directory (point it at the main checkout's .masa when running from a git worktree)
STATE="${MASA_STATE:-.masa}"
TASKS="v_expenses v_loglevels v_semver v_calc v_deporder v_markdown v_shop v_template v_cron"
for spec in $1; do
  model="${spec%%|*}"; rest="${spec#*|}"; name="${rest%%|*}"; rest="${rest#*|}"; round="${rest%%|*}"; scale="${rest#*|}"
  for task in $TASKS; do
    while [ -f "$STATE/r2.pause" ]; do sleep 20; done
    [ -f "$STATE/r2.stop" ] && { echo "stopped by r2.stop"; echo "FINISHED-9"; exit 9; }
    echo "=== $(date +%H:%M:%S) $model $task round $round"
    PYTHONPATH=src PYTHONIOENCODING=utf-8 python scripts/eval/ab_eval.py --name "$name" --only "$task" --arms "${R2_ARM:-v2rL}" --models "${R2_MODE:-local}:$model" \
      --state "$STATE" --source "$STATE/p2eval_final" --round "$round" --yuan "${R2_YUAN:-0.5}" --per-run "${R2_PER_RUN:-0.3}" --scale "$scale" --max-calls 80 --minutes 45 2>&1 | grep -v "开始 …"
  done
done
curl -s -m 10 http://127.0.0.1:11434/api/ps
echo
echo "FINISHED-0"
