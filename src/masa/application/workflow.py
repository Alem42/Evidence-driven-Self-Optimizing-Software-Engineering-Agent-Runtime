"""工作流检查点规则，与 HTTP 和后台线程解耦。 Workflow checkpoint rules independent of HTTP and threads."""
import json


class WorkflowCheckpoint:
    def __init__(self, job):
        """使用既有持久任务，不创建第二份状态。 Wrap the existing durable job without duplicating state."""
        self.job = job

    def phase(self, name, rid=None, attempt=None):
        """阶段、运行和轮次作为同一检查点提交。 Commit phase, run and attempt together."""
        update = {'phase': name}
        if rid is not None:
            update['run_id'] = rid
        if attempt is not None:
            update['attempt'] = attempt
        self.job.update(update)

    def record_assertion(self, verified, signature):
        """每个验证只计数一次，跨重启保留连续失败次数。 Count each verification once across restarts."""
        signature = json.loads(json.dumps(signature))
        if self.job.get('evaluated_verification') != verified:
            # 元组与 JSON 列表统一，避免数据库重开后误判不同断言。
            # Normalize tuples and JSON lists before comparing persisted signatures.
            repeated = self.job.get('repeated_assertions', 0) + 1 if signature and signature == self.job.get('prior_assertion') else 1
            self.job.update(prior_assertion=signature, repeated_assertions=repeated,
                            evaluated_verification=verified)
        return bool(signature) and self.job.get('repeated_assertions', 0) >= 3
