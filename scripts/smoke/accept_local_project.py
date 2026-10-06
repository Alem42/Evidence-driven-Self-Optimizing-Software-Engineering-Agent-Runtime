"""本地模型生成验收，不允许云端回退。 Local generation acceptance with no cloud fallback."""
import argparse
import json
from pathlib import Path
import time
import uuid
from masa.application.orchestration.coordinator import WorkflowCoordinator
from masa.application.applications import run_application
from masa.infrastructure.store import Store
from masa.infrastructure.runner import Runner
from masa.infrastructure.settings import Settings
from masa.infrastructure.jobs import Jobs
from masa.domain.models import MasaError


def main():
    """复用批准方案，生成真实代码并保存检查/CLI证据。 Reuse an approved plan and persist real generation/check/CLI evidence."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',required=True)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--sum-probes',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];state=root/'.masa'
    provider=Settings(state).provider(args.profile)
    if provider.config['model_type']!='local':raise MasaError('this acceptance script only permits local models')
    store=Store(state);runner=Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe')
    jobs=Jobs(state);ident=uuid.uuid4().hex;plan=store.run(args.plan)
    jobs[ident]={'status':'running','run_id':args.plan,'plan_id':args.plan,'mode':'auto','phase':'generation','attempt':0,
        'started':time.time(),'provider':provider.profile,'model_snapshot':provider.snapshot,'request':{'goal':plan['data']['goal']}}
    print('LOCAL_JOB',ident,'MODEL',provider.config['model'],flush=True)
    report={}
    try:
        WorkflowCoordinator(store,runner,provider,jobs[ident]).run()
        job=dict(jobs[ident]);print('RESULT',job['status'],job.get('run_id'),flush=True)
        verified=store.run(job['run_id'])
        report['verification_status']=verified['status']
        if verified['status']!='succeeded':raise MasaError('local workflow ended without a passing Gate: '+verified['reason'])
        if job['status']=='completed' and args.sum_probes:
            probes=[]
            for arguments,code,stdout in [([],0,'0\n'),(['1','2','3'],0,'6\n'),(['-5','2'],0,'-3\n'),(['bad'],2,'')]:
                result=run_application(store,runner,job['run_id'],arguments,lambda:False)
                if result['exit_code']!=code or result['stdout']!=stdout or (code and not result['stderr']):
                    raise MasaError('independent sum CLI probe failed')
                probes.append({'arguments':arguments,'result':result})
            report['probes']=probes;print('CLI_PROBES',len(probes),'passed',flush=True)
    except Exception as exc:
        jobs[ident].update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'local acceptance failed')
        print('FAILED',jobs[ident]['error'],flush=True)
    finally:
        report['job']=dict(jobs[ident])
        (state/'local-project-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        store.close()
    if jobs[ident]['status']!='completed':raise SystemExit(1)


if __name__=='__main__':main()
