"""提供商结构约束；业务校验仍由 domain 执行。 Provider schemas never replace domain validation."""


def response_schema(context):
    """约束 Planner 规格或澄清，保留业务校验。 Constrain specs or clarification without replacing validation."""
    string={'type':'string'}
    purpose=context.get('purpose')
    if purpose in {'project_repair','project_test_revision'}:
        # 在传输层就限制修复文件类别，Domain 再独立核对冻结边界。
        # Restrict repair paths in transport; Domain independently enforces frozen boundaries.
        tests=purpose=='project_test_revision'
        paths=[p for p in context['original_files'] if p.endswith('.go') and p.endswith('_test.go')==tests]
        return {'type':'object','additionalProperties':False,'required':['files'],
                'properties':{'files':{'type':'object','additionalProperties':False,'minProperties':1,
                                     'properties':{p:string for p in paths}}}}
    if purpose=='project_developer' and context.get('generation_mode')=='files-v1':
        # 一次仅约束一个批准文件，减少整套源码语法约束的输出压力。
        # Constrain one approved file rather than an entire code bundle.
        path=context['target_path']
        return {'type':'object','additionalProperties':False,'required':['files'],
                'properties':{'files':{'type':'object','additionalProperties':False,'required':[path],
                                     'properties':{path:string}}}}
    if purpose=='project_tester':
        case={'type':'object','additionalProperties':False,'required':['name','input','expected','level'],
              'properties':{'name':string,'input':string,'expected':string,'level':{'enum':['unit','integration','cli']}}}
        check={'type':'object','additionalProperties':False,'required':['purpose','acceptance_indices'],
               'properties':{'purpose':string,
                    'acceptance_indices':{'type':'array','items':{'type':'integer','minimum':0,'maximum':len(context['spec']['acceptance'])-1}},
                    'cases':{'type':'array','minItems':1,'maxItems':16,'items':case}}}
        # 名称作为键避免重复工具，绕过本地不完整的 tuple schema 支持。
        # Named keys prevent duplicate operations without relying on unsupported tuple schemas.
        test={**check,'required':check['required']+['cases']}
        static={**check,'properties':{k:v for k,v in check['properties'].items() if k!='cases'}}
        return {'type':'object','additionalProperties':False,'required':['checks'],
                'properties':{'checks':{'type':'object','additionalProperties':False,'required':['go_test'],
                    'properties':{'go_test':test,'go_vet':static,'go_fmt_check':static}}}}
    if purpose!='project_planner':
        return 'json'
    spec={'type':'object','additionalProperties':False,
            'required':['summary','module','entrypoint','files','acceptance'],
            'properties':{'summary':string,'module':string,'entrypoint':{'type':'string','const':'cmd/app/main.go'},
                'files':{'type':'array','minItems':3,'maxItems':20,'items':{'type':'object','additionalProperties':False,
                    'required':['path','purpose'],'properties':{'path':string,'purpose':string}}},
                'acceptance':{'type':'array','minItems':1,'maxItems':12,'items':string}}}
    if not context.get('clarification_allowed'):
        return spec
    option={'type':'object','additionalProperties':False,'required':['id','label'],
            'properties':{'id':string,'label':string}}
    question={'type':'object','additionalProperties':False,'required':['key','title','options','allow_text'],
              'properties':{'key':string,'title':string,'allow_text':{'const':True},
                  'options':{'type':'array','maxItems':3,'items':option}}}
    clarification={'type':'object','additionalProperties':False,'required':['kind','reason','questions'],
                   'properties':{'kind':{'const':'clarification_request'},'reason':string,
                       'questions':{'type':'array','minItems':1,'maxItems':3,'items':question}}}
    return {'oneOf':[spec,clarification]}
