"""后台任务检查点，重启后显式续行。 Persist background checkpoints for explicit restart recovery."""
import json
import sqlite3
from contextlib import closing
from masa.domain.models import canonical


class Job(dict):
    def __init__(self, value, save):
        """更新内存视图时同步写入检查点。 Persist each mutation of the in-memory view."""
        super().__init__(value);self.save=save

    def update(self,*args,**kwargs):
        """原子保存一批字段。 Save a batch of fields atomically."""
        super().update(*args,**kwargs);self.save(self)

    def __setitem__(self,key,value):
        """保存单字段状态迁移。 Save a single-field transition."""
        super().__setitem__(key,value);self.save(self)


class Jobs(dict):
    def __init__(self,root):
        """加载历史任务，将原运行任务标记为中断而非自动重放。 Load jobs and mark unfinished workers interrupted."""
        super().__init__();self.path=root/'runtime.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS workflow_jobs(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            db.commit()
            rows=db.execute('SELECT id,data FROM workflow_jobs').fetchall()
        for ident,raw in rows:
            value=json.loads(raw)
            if value['status']=='running':value.update(status='interrupted',note='服务已重启；请恢复任务或检查未完成调用。')
            self[ident]=value

    def __setitem__(self,ident,value):
        """只保存流程元数据，不存 API 密钥。 Persist workflow metadata without API credentials."""
        def save(data):
            with closing(sqlite3.connect(self.path,timeout=10)) as db:
                db.execute('INSERT OR REPLACE INTO workflow_jobs VALUES(?,?)',(ident,canonical(dict(data))))
                db.commit()
        job=Job(value,save);super().__setitem__(ident,job);save(job)
