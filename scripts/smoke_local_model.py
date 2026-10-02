"""本地固定模型验收，不升级或调用云端。 Fixed local model acceptance without cloud escalation."""
import argparse
import json
from pathlib import Path
from masa.infrastructure.settings import Settings
from masa.infrastructure.store import Store
from masa.infrastructure.runner import Runner
from masa.runtime.engine import Runtime
from masa.domain.models import Budget


def main():
    """登记本地配置，真实调用/工具验收并保留调用账本。 Register local metadata and verify real calls/tools with durable evidence."""
    parser=argparse.ArgumentParser();parser.add_argument('--model',default='gemma4:12b');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];settings=Settings(root/'.masa');previous=settings.active_id
    ident=next((i for i,p in settings.profiles.items() if p['model_type']=='local' and p['model']==args.model),None)
    if not ident:
        result=settings.save({'new':True,'name':'Ollama · '+args.model,'model_type':'local','protocol':'ollama',
            'base_url':'http://127.0.0.1:11434','model':args.model,'level':1,'priority':0,'timeout_seconds':180,
            'max_output_tokens':4096,'context_limit':8192,'thinking':'disabled'})
        ident=result['active_id']
        if previous:settings.save({'action':'select','id':previous})
    provider=settings.provider(ident)
    probe=settings.test(ident);print('CONNECTION',probe['ok'],probe['message'],probe['usage'],flush=True)
    if not probe['ok']:raise RuntimeError('local protocol probe failed; no automatic retry')
    store=Store(root/'.masa')
    try:
        runtime=Runtime(store,Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe'),provider)
        rid=runtime.create(root/'tests/fixtures/go-pass','Run Go tests; summarize only the actual tool result.',Budget(model_calls=2,tool_calls=1,deadline_seconds=600))
        print('RUN',rid,flush=True)
        result=runtime.execute(rid)
        report={'run_id':rid,'model':args.model,'status':result['status'],'connection':probe}
        (root/'.masa/local-model-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('GATE',result['status'],result['reason'],flush=True)
        if result['status']!='succeeded':raise RuntimeError('local runtime acceptance did not pass')
    finally:store.close()


if __name__=='__main__':main()
