#!/usr/bin/env bash
# R1 的外壳：跑 r1_workload.py；被 --kill-at 硬杀（退出码 137）后自动用 --resume 重启一次，模拟“进程被 kill -9 之后人把它重新拉起来”。最后写 FINISHED-<码>。
# R1 wrapper: run r1_workload.py; after the --kill-at hard kill (exit 137) restart it once with --resume, like a human bringing the process back after kill -9. Ends with FINISHED-<code>.
# usage: bash scripts/eval/r1_run.sh <name> <models> [extra args...] >> .masa/<name>.log 2>&1
cd "$(dirname "$0")/../.." || exit 1
name="$1"; models="$2"; shift 2
PYTHONPATH=src PYTHONIOENCODING=utf-8 python scripts/eval/r1_workload.py --name "$name" --models "$models" "$@"
code=$?
if [ "$code" = "137" ]; then
  echo "=== $(date +%H:%M:%S) process was killed (137); restarting with --resume"
  extra=()
  for a in "$@"; do case "$a" in --kill-at|--kill-at=*) skip=1;; *) if [ -n "$skip" ]; then skip=; else extra+=("$a"); fi;; esac; done
  PYTHONPATH=src PYTHONIOENCODING=utf-8 python scripts/eval/r1_workload.py --name "$name" --models "$models" "${extra[@]}" --resume
  code=$?
fi
echo "FINISHED-$code"
