"""独立 CSV CLI 验收，不使用模型生成的预期结果。 Independent CSV CLI acceptance with fixed expected outcomes."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from masa.infrastructure.store import Store


def main():
    """编译已批准项目并记录黑盒边界验收。 Build an approved project and record black-box boundary evidence."""
    parser=argparse.ArgumentParser();parser.add_argument('run_id');args=parser.parse_args()
    root=Path(__file__).resolve().parents[2];store=Store(root/'.masa')
    try:
        run=store.run(args.run_id)
        if run['status']!='succeeded':raise SystemExit('Harness must pass before independent acceptance')
        env={**os.environ,'GOPROXY':'off','GOWORK':'off','GOFLAGS':'-mod=readonly'}
        cases=[
            ('refund_sort','category,amount_cents\ntravel,300\n food ,1250\nfood,-250\n',0,'category,total_cents\nfood,1000\ntravel,300\n'),
            ('header_only','category,amount_cents\n',0,'category,total_cents\n'),
            ('quoted','category,amount_cents\n"a,b",100\n"a,b",-40\n',0,'category,total_cents\n"a,b",60\n'),
            ('boundaries','category,amount_cents\nx,1000000000\ny,-1000000000\n',0,'category,total_cents\nx,1000000000\ny,-1000000000\n'),
            ('1000_records','category,amount_cents\n'+'x,1\n'*1000,0,'category,total_cents\nx,1000\n'),
            ('late_invalid','category,amount_cents\nx,12\ny,nope\n',2,''),
            ('empty_category','category,amount_cents\n  ,1\n',2,''),
            ('wrong_header','amount_cents,category\n',2,''),
            ('malformed','category,amount_cents\n"unterminated,1\n',2,''),
            ('above_bound','category,amount_cents\nx,1000000001\n',2,''),
            ('below_bound','category,amount_cents\nx,-1000000001\n',2,''),
            ('1001_records','category,amount_cents\n'+'x,1\n'*1001,2,''),
            ('empty_file','',2,''),
        ]
        results=[]
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);exe=folder/'expense.exe'
            subprocess.run([str(root/'.tools/go/bin/go.exe'),'build','-o',str(exe),'./cmd/app'],cwd=run['data']['workspace'],env=env,check=True,capture_output=True,timeout=60)
            for name,content,code,output in cases:
                file=folder/'input.csv';file.write_text(content,encoding='utf-8')
                p=subprocess.run([str(exe),str(file)],capture_output=True,text=True,timeout=5,env=env)
                passed=p.returncode==code and p.stdout==output and (bool(p.stderr) if code else not p.stderr)
                results.append({'name':name,'passed':passed,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
            for name,argv in [('no_args',[]),('extra_args',['a','b']),('missing_file',[str(folder/'missing.csv')])]:
                p=subprocess.run([str(exe),*argv],capture_output=True,text=True,timeout=5,env=env)
                results.append({'name':name,'passed':p.returncode==2 and not p.stdout and bool(p.stderr),'exit_code':p.returncode})
        ref=store.put({'method':'independent expense CLI acceptance v1','cases':results})
        store.event(args.run_id,'external_acceptance_completed',{'acceptance_ref':ref,'passed':all(r['passed'] for r in results)})
        for result in results:print(result['name'], 'PASS' if result['passed'] else 'FAIL')
        if not all(r['passed'] for r in results):raise SystemExit(1)
    finally:store.close()


if __name__=='__main__':main()
