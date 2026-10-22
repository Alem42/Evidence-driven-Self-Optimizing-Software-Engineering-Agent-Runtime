"""控制台的评测部分：题库、启动/停止、状态与结果。 The console's benchmark part."""
import threading
from masa.domain.models import MasaError


class BenchMixin:
    # ───────────── 评测 / benchmark ─────────────
    def bench_tasks(self):
        from masa.bench.tasks import describe
        return describe()

    def bench_start(self, body):
        """启动一次评测（后台线程）。评测在独立的临时状态目录里跑，不污染历史，也不占用主界面的任务槽，但会占用 GPU 和云端额度，所以不允许与正在执行的任务同时跑。
        Start a benchmark in a background thread. It runs in a scratch state directory (no pollution of history) but uses the GPU and cloud budget, so it never overlaps a running task."""
        from masa.bench.runner import BenchRunner, resolve_config
        with self.lock:
            if self.bench_thread and self.bench_thread.is_alive():
                raise MasaError('a benchmark is already running')
            if (self.job_thread and self.job_thread.is_alive()) or self.active:
                raise MasaError('a task is running; start the benchmark after it finishes')
            config=resolve_config(body)
            self.bench=BenchRunner(self.root,self.runner_path,self.go_path,self.project,config)
            self.bench_thread=threading.Thread(target=self.bench.run,daemon=True,name='masa-bench')
            self.bench_thread.start()
            return {'id':self.bench.id}

    def bench_status(self):
        return self.bench.snapshot() if self.bench else {'state':'idle'}

    def bench_stop(self):
        if self.bench and self.bench_thread and self.bench_thread.is_alive():
            self.bench.stop()
        return self.bench_status()

    def bench_results(self):
        from masa.bench.runner import list_results
        return {'results':list_results(self.root)}

    def bench_result(self, result_id):
        from masa.bench.runner import load_result
        from masa.bench.report import aggregate
        data=load_result(self.root,result_id)
        data['aggregate']=aggregate(data.get('records',[]))
        return data
