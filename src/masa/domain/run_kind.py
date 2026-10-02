"""运行用途，不引入第二套状态。 Run purposes without a second state machine."""


def run_kind(data):
    """从业务内容识别用途；旧记录无须改写。 Infer purpose from business content without rewriting legacy records."""
    if data.get('semantic_review') is not None:
        return 'semantic_review'
    if data.get('project_bundle') is not None:
        return 'project_verification'
    if data.get('project_plan') is not None:
        return 'project_draft' if data['project_plan'].get('kind') == 'code' else 'project_planning'
    if data.get('codegen') is not None:
        return 'legacy_single_file'
    return 'tool_verification'
