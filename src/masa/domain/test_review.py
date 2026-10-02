"""独立模型评审结果契约。 Independent model review result contract."""
from masa.domain.models import MasaError
from masa.domain.proposals import text


def review_sources(spec, checks):
    """分配确定性引用标识，模型无需计算数组索引。 Assign deterministic source IDs instead of model-computed indices."""
    sources={f'acceptance:{i}':value for i,value in enumerate(spec['acceptance'])}
    for ci,check in enumerate(checks):
        sources[f'check:{ci}:purpose']=check['purpose']
        for ti,case in enumerate(check.get('cases',[])):
            for field,value in case.items():
                if value:sources[f'case:{ci}:{ti}:{field}']=value
    return sources


def validate_semantic_review(value, spec, checks):
    """要求意见引用输入事实，不能把无出处意见当作证据。 Require findings to cite actual supplied text."""
    if not isinstance(value,dict) or set(value)!={'summary','findings'}:raise MasaError('invalid semantic review fields')
    text(value['summary'],2000)
    if not isinstance(value['findings'],list) or len(value['findings'])>12:raise MasaError('invalid semantic review findings')
    for f in value['findings']:
        if isinstance(f,dict) and 'source_id' in f:
            required={'severity','source_id','explanation','suggestion'}
            if set(f) not in (required,required|{'evidence'}):raise MasaError('invalid source review finding')
            if f['severity'] not in {'warning','blocking'} or not isinstance(f['source_id'],str):raise MasaError('invalid source review fields')
            sources=review_sources(spec,checks)
            if f['source_id'] not in sources:raise MasaError('unknown review source id')
            resolved=sources[f['source_id']]
            if 'evidence' in f and f['evidence']!=resolved:raise MasaError('resolved review evidence mismatch')
            for field in ('explanation','suggestion'):text(f[field],2000)
            f['evidence']=resolved
            continue
        common={'severity','acceptance_index','check_index','case_index','explanation','suggestion'}
        if not isinstance(f,dict) or set(f) not in (common|{'evidence'},common|{'evidence_field'},common|{'evidence_field','evidence'}):
            raise MasaError('invalid semantic review finding')
        ai,ci,ti=f['acceptance_index'],f['check_index'],f['case_index']
        if f['severity'] not in {'warning','blocking'} or type(ai) is not int or not 0<=ai<len(spec['acceptance']):raise MasaError('invalid review acceptance reference')
        sources=[spec['acceptance'][ai]]
        if ci is not None:
            if type(ci) is not int or not 0<=ci<len(checks):raise MasaError('invalid review check reference')
            sources.append(checks[ci]['purpose'])
            if ti is not None:
                cases=checks[ci].get('cases',[])
                if type(ti) is not int or not 0<=ti<len(cases):raise MasaError('invalid review case reference')
                sources.extend(str(v) for v in cases[ti].values())
        elif ti is not None:raise MasaError('case reference requires check reference')
        if 'evidence_field' in f:
            # 模型选择字段，原文由 Runtime 提取；不依赖模型重述引用。
            # Resolve the selected field directly instead of trusting model quotation.
            field=f['evidence_field']
            if field=='acceptance':resolved=spec['acceptance'][ai]
            elif field=='purpose' and ci is not None:resolved=checks[ci]['purpose']
            elif field in {'name','input','expected','level'} and ci is not None and ti is not None:resolved=checks[ci]['cases'][ti][field]
            else:raise MasaError('invalid review evidence field')
            if 'evidence' in f and f['evidence']!=resolved:raise MasaError('resolved review evidence mismatch')
            f['evidence']=resolved
        for k in ('evidence','explanation','suggestion'):text(f[k],2000)
        if not any(f['evidence'] in source for source in sources):raise MasaError('review evidence is absent from referenced input')
    return value
