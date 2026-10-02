"""真实随机数闭环与独立 CLI 验收。 Live random CLI workflow and independent acceptance probes.

默认调用本地配置的付费 API；--run 复验现有成功版本，不再调用模型。
Default runs the configured paid API; --run rechecks a successful version without model calls.
"""
import argparse
import json
from pathlib import Path
import re
import time

from masa.application.console import Console
from masa.application.applications import run_application
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store


def verify(store, runner, rid):
    """通过同一 harness 独立检查输出、边界和退出码。 Independently probe output, bounds and exits through the harness."""
    results=[]
    def probe(args, code=0, count=1, bounds=(1,100)):
        result=run_application(store,runner,rid,args,lambda:False)
        assert result['status']=='completed' and result['exit_code']==code, (args,result)
        stdout=result['stdout'];stderr=result['stderr']
        if code:
            assert stdout=='' and stderr.strip(),(args,result)
        else:
            assert not stderr and re.fullmatch(r'-?\d+(?: -?\d+)*\n',stdout),(args,result)
            values=list(map(int,stdout.split()))
            assert len(values)==count and all(bounds[0]<=v<=bounds[1] for v in values),(args,result)
        results.append({'args':args,'exit_code':code,'stdout':stdout,'stderr':stderr})
        return stdout
    probe([])
    probe(['-min','-20','-max','-1','-count','100'],count=100,bounds=(-20,-1))
    probe(['-min','-7','-max','-7','-count','100'],count=100,bounds=(-7,-7))
    probe(['-min','-1000000','-max','1000000','-count','100'],count=100,bounds=(-1000000,1000000))
    seeded=['-seed','42','-count','100']
    assert probe(seeded,count=100)==probe(seeded,count=100),'fixed seed is not reproducible'
    zero=['-seed','0','-count','10']
    assert probe(zero,count=10)==probe(zero,count=10),'explicit zero seed was treated as omitted'
    for args in (['-min','2','-max','1'],['-count','0'],['-count','101'],['-min','-1000001'],
                 ['-max','1000001'],['-min','bad'],['-max','bad'],['-count','bad'],['-seed','bad'],['-count']):
        probe(args,2)
    return results


def main():
    """保存任务身份和完整验收结果，打印进度但不打印配置密钥。 Persist job identity and acceptance results without exposing credentials."""
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group()
    group.add_argument('--run');group.add_argument('--repair-tests',help='explicit new test revision of a failed verification; calls the real API')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];state=root/'.masa'
    runner=Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe')
    rid=args.run;job=None
    if args.repair_tests:
        from masa.application.generation import ProjectGeneration
        from masa.infrastructure.settings import Settings
        from masa.runtime.engine import Runtime
        store=Store(state)
        try:
            service=ProjectGeneration(store,runner)
            draft=service.revise_tests(args.repair_tests,Settings(state).provider(),
                'Fix CLI test setup only: build and execute an absolute binary path in t.TempDir(); preserve all behavioral assertions.')
            meta=store.run(draft)['data']['project_plan']
            rid=service.approve(draft,{'files_ref':meta['files_ref'],'files':store.read(meta['files_ref'])})
            print('TEST_REVISION',draft,'VERIFICATION',rid,flush=True)
            result=Runtime(store,runner).execute(rid)
            print('GATE',result['status'],flush=True)
            if result['status']!='succeeded':raise RuntimeError('revised tests still fail; inspect evidence')
        finally:store.close()
    elif not rid:
        console=Console(state,runner.executable,runner.go_executable,root)
        goal=(root/'docs/scenarios/SCENARIO_SEEDED_RANDOM.md').read_text(encoding='utf-8')
        ident=console.start_autonomous_project_job({'goal':goal})['job_id']
        print('JOB',ident,flush=True);last=None
        while console.job_thread.is_alive():
            job=dict(console.jobs[ident]);progress=(job.get('phase'),job.get('run_id'),job.get('attempt'))
            if progress!=last:print('PROGRESS',progress,flush=True);last=progress
            console.job_thread.join(timeout=2)
        job=dict(console.jobs[ident]);console.close()
        (state/'seeded-random-job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        print('RESULT',job['status'],job.get('error'),job.get('run_id'),flush=True)
        if job['status']!='completed':raise RuntimeError('workflow stopped; inspect saved job and evidence')
        rid=job['run_id']
    store=Store(state)
    try:
        probes=verify(store,runner,rid)
        report={'run_id':rid,'probes':probes,'job':job}
        (state/'seeded-random-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('ACCEPTANCE',rid,len(probes),'probes passed',flush=True)
    finally:store.close()


if __name__=='__main__':main()
