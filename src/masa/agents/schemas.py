"""提供商结构约束；业务校验仍由 domain 执行。 Provider schemas never replace domain validation."""


def response_schema(context):
    """约束 Planner 规格或澄清，保留业务校验。 Constrain specs or clarification without replacing validation."""
    string={'type':'string'}
    purpose=context.get('purpose')
    if purpose=='project_tester':
        case={'type':'object','additionalProperties':False,'required':['name','input','expected','level'],
              'properties':{'name':string,'input':string,'expected':string,'level':{'enum':['unit','integration','cli']}}}
        check={'type':'object','additionalProperties':False,'required':['operation','purpose','acceptance_indices'],
               'properties':{'operation':{'enum':['go_test','go_vet','go_fmt_check']},'purpose':string,
                    'acceptance_indices':{'type':'array','items':{'type':'integer','minimum':0,'maximum':len(context['spec']['acceptance'])-1}},
                    'cases':{'type':'array','minItems':1,'maxItems':16,'items':case}}}
        return {'type':'object','additionalProperties':False,'required':['checks'],
                'properties':{'checks':{'type':'array','minItems':1,'maxItems':3,'items':check}}}
    if purpose=='project_developer':
        paths=[f['path'] for f in context['spec']['files']]
        return {'type':'object','additionalProperties':False,'required':['files'],
                'properties':{'files':{'type':'object','additionalProperties':False,'required':paths,
                    'properties':{p:string for p in paths}}}}
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
