"""按失败位置选择修复材料。 Select repair context from failures and syntax evidence."""
import re
from masa.intelligence.index import Intelligence
from masa.intelligence.context import ContextBuilder
from masa.domain.models import canonical


def build_repair_context(store, executor, rid, files, evidence, feedback):
    """保留诊断文件、模块和测试，补充 AST 候选并记录遗漏。 Keep diagnostic files, module/tests and AST candidates with an omission manifest."""
    query='\n'.join(line for item in evidence for line in item.get('diagnostics',[]))+'\n'+feedback
    engine=Intelligence(store,executor)
    index=engine.build(rid)
    context,manifest=ContextBuilder(store,engine).build(rid,index,role='developer',query=query[:4000],budget_bytes=32000)
    normalized=query.replace('\\','/')
    directly_named={path for path in files if path in normalized or
                    re.search(r'(?<![\w/])'+re.escape(path.rsplit('/',1)[-1])+r':\d+',normalized)}
    selected=set(directly_named)
    selected.update(piece['path'] for piece in context['code'])
    # 没有定位证据时回退完整输入，不能仅因预算把实现猜掉。
    # Fall back to full input when there is no useful localization evidence.
    if not directly_named:selected=set(files)
    selected.update(path for path in files if path=='go.mod' or path.endswith('_test.go'))
    chosen={path:value for path,value in files.items() if path in selected}
    report={'policy':'diagnostic-and-ast-v1','snapshot_id':store.run(rid)['data']['snapshot_id'],
            'selected':list(chosen),'omitted':[p for p in files if p not in chosen],
            'original_bytes':len(canonical(files).encode()),'selected_bytes':len(canonical(chosen).encode()),
            'context_manifest_ref':store.put(manifest),
            'limits':'syntax candidates only; missing context requires a new scoped revision, not guessing'}
    store.event(rid,'repair_context_selected',report)
    return chosen,report
