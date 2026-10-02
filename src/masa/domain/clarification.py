"""需求澄清协议校验。 Requirement clarification contract validation."""
from masa.domain.models import MasaError


def validate_question(value):
    """限制问题大小和选项，保留自由回答入口。 Bound questions and options while allowing free text."""
    if not isinstance(value,dict) or set(value)!={'kind','reason','questions'} or value['kind']!='clarification_request':
        raise MasaError('invalid clarification request')
    def bounded(s,n):
        return isinstance(s,str) and bool(s.strip()) and len(s)<=n
    if not bounded(value['reason'],1000) or not isinstance(value['questions'],list) or not 1<=len(value['questions'])<=3:
        raise MasaError('invalid clarification questions')
    keys=set()
    for q in value['questions']:
        if not isinstance(q,dict) or set(q)!={'key','title','options','allow_text'}:
            raise MasaError('invalid clarification question')
        if not bounded(q['key'],80) or q['key'] in keys or not bounded(q['title'],500) or q['allow_text'] is not True:
            raise MasaError('invalid clarification question fields')
        keys.add(q['key'])
        if not isinstance(q['options'],list) or len(q['options'])>3:
            raise MasaError('invalid clarification options')
        ids=set()
        for o in q['options']:
            if not isinstance(o,dict) or set(o)!={'id','label'} or not bounded(o['id'],80) or not bounded(o['label'],300) or o['id'] in ids:
                raise MasaError('invalid clarification option')
            ids.add(o['id'])
    return value


def validate_answers(question, answers):
    """每问必须明确选择或填写，空值不是同意。 Require an explicit choice or text for every question."""
    if not isinstance(answers,dict) or set(answers)!={q['key'] for q in question['questions']}:
        raise MasaError('answer every clarification question')
    for q in question['questions']:
        a=answers[q['key']]
        if not isinstance(a,dict) or len(a)!=1:
            raise MasaError('choose an option or provide text')
        if 'option_id' in a:
            if a['option_id'] not in [o['id'] for o in q['options']]:raise MasaError('unknown clarification option')
        elif 'text' not in a or not isinstance(a['text'],str) or not a['text'].strip() or len(a['text'])>2000:
            raise MasaError('invalid clarification answer text')
    return answers
