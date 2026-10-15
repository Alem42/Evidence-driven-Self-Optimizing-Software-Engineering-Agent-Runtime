"""角色提示词与输出 schema 的“黄金样本”上下文。迁移到 RoleSpec 注册表前后，这些上下文的渲染结果必须逐字节一致。
Contexts for the role prompt / output-schema golden samples. Rendering them must be byte-identical before and after the RoleSpec registry migration.

重新生成黄金样本（只在“有意修改提示词”时做）：python tests/golden/capture_roles.py
Regenerate the golden file (only when deliberately changing a prompt): python tests/golden/capture_roles.py
"""

SPEC = {'summary': 's', 'module': 'example.com/task', 'entrypoint': 'cmd/app/main.go',
        'files': [{'path': 'go.mod', 'purpose': 'm'}, {'path': 'cmd/app/main.go', 'purpose': 'main'},
                  {'path': 'internal/app/app.go', 'purpose': 'logic'}, {'path': 'internal/app/app_test.go', 'purpose': 'tests'}],
        'acceptance': ['prints hello', 'exit code 0', 'no input read']}
CHECKS = [{'operation': 'go_test', 'purpose': 'unit', 'acceptance_indices': [0, 1],
           'cases': [{'name': 'c', 'input': '', 'expected': 'hello', 'level': 'cli'}]},
          {'operation': 'go_vet', 'purpose': 'vet', 'acceptance_indices': [2]}]
FILES = {'go.mod': 'module example.com/task\n\ngo 1.27.0\n', 'cmd/app/main.go': 'package main\n', 'internal/app/app.go': 'package app\n',
         'internal/app/app_test.go': 'package app\n'}

# 名字 → 上下文。覆盖每个角色的每一种提示词分支。 name -> context, covering every prompt branch of every role.
CONTEXTS = {
    'planner': {'purpose': 'project_planner', 'goal': 'g'},
    'planner_clarification': {'purpose': 'project_planner', 'goal': 'g', 'clarification_allowed': True},
    'tester': {'purpose': 'project_tester', 'goal': 'g', 'spec': SPEC},
    'developer_bundle': {'purpose': 'project_developer', 'goal': 'g', 'spec': SPEC},
    'developer_files': {'purpose': 'project_developer', 'goal': 'g', 'spec': SPEC, 'generation_mode': 'files-v1',
                        'target_path': 'internal/app/app.go', 'previous_files': {}},
    'repair': {'purpose': 'project_repair', 'goal': 'g', 'spec': SPEC, 'original_files': FILES, 'failure_evidence': [], 'feedback': ''},
    'test_revision': {'purpose': 'project_test_revision', 'goal': 'g', 'spec': SPEC, 'original_files': FILES, 'failure_evidence': [], 'feedback': ''},
    'diagnoser': {'purpose': 'project_diagnoser', 'goal': 'g'},
    'triage': {'purpose': 'project_triage', 'goal': 'g'},
    'test_reviewer_legacy': {'purpose': 'project_test_reviewer', 'spec': SPEC, 'checks': CHECKS},
    'test_reviewer_sources': {'purpose': 'project_test_reviewer', 'protocol_version': 'sources-v1', 'spec': SPEC, 'checks': CHECKS, 'sources': {}},
    'code_generation': {'purpose': 'code_generation'},
    'verifier': {'purpose': 'verifier'},
    'unregistered': {'purpose': 'something_new'},
    'no_purpose': {},
}
