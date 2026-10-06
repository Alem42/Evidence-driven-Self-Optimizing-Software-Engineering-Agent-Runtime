"""独立于测试生成与实现的测试计划审查。 Test-plan review independent of test authoring and implementation."""
import re
import hashlib
from masa.domain.models import canonical


def review_test_plan(spec, checks):
    """检测可确定的测试缺陷，不推断未知业务或冒充执行证据。 Detect bounded issues without inventing requirements or execution results."""
    findings=[]
    def add(code,severity,message,check_index=None,case_index=None):
        findings.append({'code':code,'severity':severity,'message':message,
                         'check_index':check_index,'case_index':case_index})
    missing=sorted(set(range(len(spec['acceptance'])))-{i for c in checks for i in c['acceptance_indices']})
    if missing:
        add('coverage_gap','warning','验收项缺少检查引用：'+', '.join(str(i+1) for i in missing))
    for ci,check in enumerate(checks):
        if check['operation']!='go_test':continue
        cases=check.get('cases',[])
        if not cases:
            add('no_concrete_cases','warning','缺少具体输入和预期，覆盖引用无法说明如何验证。',ci)
        seen=set()
        for ti,case in enumerate(cases):
            expected=case['expected']
            combined=case['name']+' '+case['input']+' '+expected
            # 普通随机采样允许重复；把“次次不同”写成断言会产生偶发失败。
            # Ordinary random sampling allows repeats; always-distinct assertions are flaky.
            random_context=re.search(r'(?i)random|随机',combined+' '+spec['summary'])
            distinct=re.search(r'(?i)(?:every|each|all|multiple).*?(?:different|distinct|unique)|(?:always|must).*?(?:different|unique)|每次.*?(?:不同|不重复)|多次.*?(?:不同|不重复)',expected)
            if random_context and distinct:
                add('random_distinct_assertion','blocking','要求每次随机输出不同不可靠；如需不放回抽样，应明确契约与容量边界，否则验证范围和格式。',ci,ti)
            if re.search(r'(?i)(?:call|use).*?(?:implementation|actual function).*?(?:expected|expectation)|调用.*?实现.*?预期',expected):
                add('circular_oracle','blocking','预期不能由被测实现计算；请给出独立预期或可验证性质。',ci,ti)
            identity=(case['input'],expected,case['level'])
            if identity in seen:add('duplicate_case','warning','相同输入、预期和测试层级重复，未增加覆盖。',ci,ti)
            seen.add(identity)
    return {'policy_version':'test-plan-review-v1','input_digest':hashlib.sha256(canonical({'spec':spec,'checks':checks}).encode()).hexdigest(),
            'status':'blocked' if any(f['severity']=='blocking' for f in findings) else 'reviewed',
            'findings':findings,'scope':'deterministic plan heuristics; not semantic proof or tool evidence'}
