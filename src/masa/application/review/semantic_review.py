"""独立模型测试计划评审，不赋予修改或批准权限。 Independent model test review without edit or approval authority."""
from pathlib import Path
import time
from masa.domain.models import Budget,MasaError
from masa.domain.proposals import validate_spec,validate_checks
from masa.domain.test_review import validate_semantic_review,review_sources
from masa.runtime.engine import Runtime
from masa.runtime.graph import harness_policy
from masa.runtime.roles import RoleRuntime


def review_test_semantics(store, runner, provider, parent, spec, checks, on_created=None, resume_id=None):
    """独立创建一次有预算评审，保存来源与结果，支持响应落盘后恢复。 Create a bounded review with durable input and recovery."""
    validate_spec(spec);validate_checks(checks,spec,require_coverage=False)
    source=store.run(parent)
    values={'goal':source['data']['goal'],'spec':spec,'checks':checks}
    if not resume_id:
        values.update(protocol_version='sources-v1',sources=review_sources(spec,checks))
    else:
        saved_meta=store.run(resume_id)['data'].get('semantic_review',{})
        if not saved_meta.get('input_ref'):raise MasaError('run is not a semantic review')
        saved_values=store.read(saved_meta['input_ref'])
        if saved_values.get('protocol_version')=='sources-v1':
            values.update(protocol_version='sources-v1',sources=review_sources(spec,checks))
    input_ref=store.put(values)
    if resume_id:
        review=store.run(resume_id);meta=review['data'].get('semantic_review',{})
        if review['data'].get('parent_run_id')!=parent or meta.get('input_ref')!=input_ref or meta.get('provider')!=provider.profile:
            raise MasaError('semantic review input or provider changed')
        if review['cancel_requested'] or time.time()>=review['data']['deadline_at']:raise MasaError('semantic review cancelled or deadline expired')
        if meta.get('status')=='completed':return {'review_id':resume_id,'report':store.read(meta['report_ref'])}
        if meta.get('status')=='failed':raise MasaError('semantic review failed; create an explicit new review')
        rid=resume_id
    else:
        meta={'status':'reviewing','input_ref':input_ref,'provider':provider.profile}
        rid=Runtime(store,runner).create(Path(source['data']['workspace']),source['data']['goal'],
            Budget(model_calls=1,tool_calls=1,deadline_seconds=86400),graph=harness_policy(),parent_run_id=parent,semantic_review=meta)
    if on_created:on_created(rid)
    try:
        output=RoleRuntime(store).call(rid,provider,'project_test_reviewer',values)
        validate_semantic_review(output,spec,checks)
    except Exception as exc:
        # 失败状态与事件原子保存，不留下仍在评审的业务状态。
        # Atomically persist failure instead of leaving the business state reviewing.
        error=str(exc) if isinstance(exc,MasaError) else 'semantic review processing failed'
        meta.update(status='failed',error=error)
        with store.transaction():
            store.save_metadata(rid,'semantic_review',meta,'semantic_review_failed',status='failed',reason=error)
            store._event(parent,'semantic_review_failed',{'review_id':rid,'input_ref':input_ref,'error':error})
        raise MasaError(f'semantic review failed; run {rid}: {error}') from None
    report={'input_ref':input_ref,**output,'status':'needs_attention' if any(f['severity']=='blocking' for f in output['findings']) else 'reviewed',
            'scope':'model advice; no edits, approval, execution or Gate verdict'}
    meta.update(status='completed',report_ref=store.put(report))
    with store.transaction():
        store.save_metadata(rid,'semantic_review',meta,'semantic_review_completed',status='paused',reason='test review advice recorded')
        store._event(parent,'semantic_review_available',{'review_id':rid,'input_ref':input_ref,'report_ref':meta['report_ref']})
    return {'review_id':rid,'report':report}
