"""协调器各部分共用的异常与轮数常量。 Exceptions and round limits shared by the coordinator and its mixins."""
from masa.domain.models import MasaError


class NoChange(MasaError):
    """这一次修复没有改动任何文件（已记入失败链）；由工作流决定下一步（诊断 / 换更强的模型重试 / 停止）。
    This fix changed nothing (already recorded as a failure); the workflow decides what next."""


class NoProgress(MasaError):
    """修复/测试修订原样返回了文件（没有任何改动），且已无更强的模型可换。重新验证同样的内容没有意义。
    The fix returned the files unchanged and no stronger model is left; re-verifying identical content is pointless."""


# 自动修复的基础轮数，以及“持续有进展”时最多额外多给几轮。
# Base number of automatic repair rounds, and how many extra rounds steady progress can earn.
BASE_ROUNDS = 4
MAX_PROGRESS_BONUS = 4
# 补丁连续这么多轮没有改善（且最强模型试过）就整体重写；每个任务最多重写几次。
# Rewrite after this many consecutive stalled patch rounds (strongest model already tried); at most this many rewrites per task.
REWRITE_AFTER_STALLS = 2
MAX_REWRITES = 1
