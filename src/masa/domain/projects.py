"""项目和阶段的读取契约；不引入第二套执行状态。 Read contracts without a second execution state machine."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    title: str
    current_run_id: str
    status: str
    revision_count: int

    def to_dict(self):
        """转换成前端读取契约。 Serialize the project read contract."""
        return asdict(self)
