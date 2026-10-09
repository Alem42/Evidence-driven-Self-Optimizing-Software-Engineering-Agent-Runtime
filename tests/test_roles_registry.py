"""RoleSpec 注册表：行为不变（黄金样本）、加载时校验、数据驱动的新角色。
RoleSpec registry: unchanged behaviour (golden samples), load-time validation, and data-only new roles."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'golden'))

from role_contexts import CONTEXTS  # noqa: E402

from masa.agents import protocol  # noqa: E402
from masa.agents.protocol import instruction_for, validate_response  # noqa: E402
from masa.agents.schemas import response_schema  # noqa: E402
from masa.application.orchestration import routing  # noqa: E402
from masa.domain.models import MasaError  # noqa: E402
from masa.infrastructure.llm import validate_config  # noqa: E402
from masa.roles import registry  # noqa: E402
from masa.roles.jsonschema import SchemaError, validate  # noqa: E402

GOLDEN = json.loads((HERE / 'golden' / 'roles_golden.json').read_text(encoding='utf-8'))


class BehaviourIsUnchangedTests(unittest.TestCase):
    """迁移前抓取的黄金样本：现有 7 个角色（加历史角色和默认指令）的渲染结果必须逐字节一致。
    Golden samples captured BEFORE the migration: every existing role's rendering must stay byte-identical."""

    def test_every_prompt_branch_renders_exactly_as_before(self):
        for name, context in CONTEXTS.items():
            with self.subTest(name):
                self.assertEqual(instruction_for(context), GOLDEN[name]['instruction'])

    def test_every_output_schema_is_identical_including_property_order(self):
        for name, context in CONTEXTS.items():
            with self.subTest(name):
                got = response_schema(context)
                self.assertEqual(got, GOLDEN[name]['schema'])
                # 属性顺序就是约束解码的生成顺序（诊断的“先核对后下结论”靠它）。 Property order is the generation order under constrained decoding.
                self.assertEqual(json.dumps(got, ensure_ascii=False), json.dumps(GOLDEN[name]['schema'], ensure_ascii=False))

    def test_the_sets_the_router_and_config_derive_equal_the_old_hard_coded_ones(self):
        """P2 在后面追加了三个可选角色（指挥者、测试怀疑者、代码审阅者）：旧的 6 个角色的集合与顺序保持不变。
        P2 appended three optional roles (conductor, test skeptic, code reviewer): the six legacy roles keep their set and order."""
        current = registry.current()
        legacy = ('project_planner', 'project_tester', 'project_developer', 'project_repair', 'project_test_revision', 'project_diagnoser')
        optional = ('project_conductor', 'test_skeptic', 'code_reviewer')
        self.assertEqual(current.routable_ids(), legacy + optional)
        # 默认的“直接用最高等级”名单与 P1 之前逐项相同（可选角色不在其中），只有 conductor 打开时才加入。 The default start-at-top list equals the pre-P1 one; optional roles join only when `conductor` is on.
        self.assertEqual(list(current.top_ids()), ['project_planner', 'project_tester', 'project_test_revision', 'project_diagnoser'])
        self.assertEqual(current.optional_top_ids(), optional)
        self.assertEqual(current.config_roles(), {'project_planner', 'project_tester', 'project_developer', 'project_repair', 'project_test_revision', 'project_test_reviewer',
                                                  'project_triage', 'project_diagnoser', 'code_generation', 'verifier', 'workflow_tuner', 'expectation_deriver', 'expectation_auditor', 'case_deriver', 'case_judge', 'package_contract', 'assertion_referee', *optional})
        self.assertEqual(routing.DEFAULT_POLICY['prefer_highest_roles'], list(current.top_ids()))

    def test_every_registered_role_names_an_existing_validator_and_builder(self):
        protocol.check_registry()  # 不抛异常 / does not raise
        for spec in registry.current().all():
            self.assertIn(spec.validator, protocol.VALIDATORS)

    def test_each_existing_role_keeps_its_labels_permissions_and_write_scope(self):
        by_id = {s.id: s for s in registry.current().all()}
        self.assertEqual(by_id['project_repair'].permissions['writes'], 'implementation')
        self.assertEqual(by_id['project_test_revision'].permissions['writes'], 'tests')
        for read_only in ('project_planner', 'project_tester', 'project_diagnoser', 'project_triage'):
            self.assertEqual(by_id[read_only].permissions['writes'], 'none')
        self.assertFalse(by_id['project_triage'].routable)  # 预检只用本地模型，不进路由器 / triage is local-only, never routed


def write_role(folder, spec_id, **over):
    meta = {'id': spec_id, 'label': 'Test 角色', 'description': '一句话描述', 'order': 500, 'level': 'top', 'routable': True, 'config_allowed': True,
            'permissions': {'reads': ['goal'], 'writes': 'none'}, 'validator': 'json_schema', 'prompt': f'{spec_id}.prompt.md',
            'output_schema': {'type': 'object', 'additionalProperties': False, 'required': ['verdict'],
                              'properties': {'verdict': {'enum': ['ok', 'bad']}, 'notes': {'type': 'string', 'maxLength': 20}}}}
    meta.update(over)
    meta = {k: v for k, v in meta.items() if v is not None}
    Path(folder, f'{spec_id}.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    if meta.get('prompt') == f'{spec_id}.prompt.md':  # 其它名字的提示词文件故意不创建（用来测“文件缺失”） / other names are deliberately not created (to test a missing file)
        Path(folder, meta['prompt']).write_text('You are a skeptical reviewer. Return {"verdict":"ok"|"bad"}.\n', encoding='utf-8')


class LoadTimeValidationTests(unittest.TestCase):
    def folder(self, **kw):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        write_role(temp.name, 'project_skeptic', **kw)
        return temp.name

    def test_a_valid_definition_loads_and_a_trailing_editor_newline_is_not_part_of_the_prompt(self):
        spec = registry.load_directory(self.folder()).get('project_skeptic')
        self.assertEqual(spec.prompt, 'You are a skeptical reviewer. Return {"verdict":"ok"|"bad"}.')

    def test_every_malformed_definition_is_rejected_when_loading_not_at_run_time(self):
        cases = {
            'bad id': {'id': 'Bad-Id'},
            'unknown field (typo)': {'descripton': 'x'},
            'empty label': {'label': ' '},
            'description too long for the conductor': {'description': 'x' * 121},
            'order is not an int': {'order': '1'},
            'bad level': {'level': 'middle'},
            'routable not a bool': {'routable': 'yes'},
            'bad write scope': {'permissions': {'reads': [], 'writes': 'everything'}},
            'both prompt and builder': {'prompt_builder': 'project_roles'},
            'missing prompt file': {'prompt': 'nope.prompt.md'},
            'schema not an object': {'output_schema': []},
            'max_output_tokens out of range': {'max_output_tokens': 0},
            'when without a guard name': {'when': {'after_round': 1}},
            'tools not a list of strings': {'tools': 'run_tests'},
        }
        for name, override in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                spec_id = override.get('id', 'project_skeptic')
                override = {k: v for k, v in override.items() if k != 'id'}
                if name == 'bad id':
                    write_role(temp, 'project_skeptic')
                    Path(temp, 'project_skeptic.json').write_text(json.dumps({**json.loads(Path(temp, 'project_skeptic.json').read_text(encoding='utf-8')), 'id': spec_id}), encoding='utf-8')
                else:
                    write_role(temp, spec_id, **override)
                with self.assertRaises(registry.RoleSpecError):
                    registry.load_directory(temp)

    def test_neither_prompt_nor_builder_and_duplicate_or_empty_directories_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            write_role(temp, 'project_skeptic', prompt=None)
            with self.assertRaises(registry.RoleSpecError):
                registry.load_directory(temp)
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(registry.RoleSpecError):
            registry.load_directory(temp)  # 空目录 / empty directory

    def test_a_file_name_that_does_not_match_the_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            write_role(temp, 'project_skeptic')
            Path(temp, 'project_skeptic.json').rename(Path(temp, 'project_other.json'))
            with self.assertRaises(registry.RoleSpecError):
                registry.load_directory(temp)

    def test_an_unknown_validator_or_builder_is_caught_when_the_registry_is_checked(self):
        for override in ({'validator': 'does_not_exist'}, {'prompt': None, 'prompt_builder': 'does_not_exist'}):
            with self.subTest(override=str(override)), tempfile.TemporaryDirectory() as temp:
                write_role(temp, 'project_skeptic', **override)
                with registry.use(temp), self.assertRaises(registry.RoleSpecError):
                    protocol.check_registry()


class DataOnlyRoleTests(unittest.TestCase):
    """验收标准：只加 JSON + Markdown，不改任何 Python，新角色就被路由器、配置校验、提示词、输出契约和校验器接受。
    Acceptance: adding only a JSON and a Markdown file makes the router, config validation, prompt, output contract and validator accept the new role."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # 复制现有角色，再加一个新角色 / copy the existing roles, then add a new one
        for path in registry.SPEC_DIR.iterdir():
            Path(temp.name, path.name).write_bytes(path.read_bytes())
        write_role(temp.name, 'project_skeptic')
        context_manager = registry.use(temp.name)
        context_manager.__enter__()
        self.addCleanup(context_manager.__exit__, None, None, None)

    def test_the_router_and_the_model_config_accept_the_new_role(self):
        self.assertIn('project_skeptic', registry.current().routable_ids())
        policy = routing.validate_policy({'prefer_highest_roles': ['project_skeptic'], 'start_level_by_role': {'project_skeptic': 2}})
        self.assertEqual(policy['prefer_highest_roles'], ['project_skeptic'])
        config = {'base_url': 'http://127.0.0.1:11434', 'model': 'm', 'model_type': 'local', 'protocol': 'ollama', 'roles': ['project_skeptic']}
        self.assertEqual(validate_config(config)['roles'], ['project_skeptic'])

    def test_an_unknown_role_is_still_rejected(self):
        with self.assertRaises(MasaError):
            routing.validate_policy({'prefer_highest_roles': ['project_ghost']})

    def test_prompt_and_output_contract_come_from_the_files(self):
        context = {'purpose': 'project_skeptic', 'goal': 'g'}
        self.assertTrue(instruction_for(context).startswith('You are a skeptical reviewer'))
        self.assertEqual(response_schema(context)['required'], ['verdict'])

    def test_the_generic_validator_enforces_the_contract_and_redacts_secrets(self):
        context = {'purpose': 'project_skeptic'}
        self.assertEqual(validate_response(context, {'verdict': 'ok'}, 'KEY'), {'verdict': 'ok'})
        self.assertEqual(validate_response(context, {'verdict': 'ok', 'notes': 'my KEY here'}, 'KEY')['notes'], 'my [REDACTED] here')  # 先脱敏再校验 / redact first
        for bad in ({'verdict': 'maybe'}, {}, {'verdict': 'ok', 'extra': 1}, {'verdict': 'ok', 'notes': 'x' * 21}):
            with self.subTest(bad=str(bad)), self.assertRaises(MasaError):
                validate_response(context, bad, 'KEY')
        with self.assertRaises(MasaError):
            validate_response(context, ['verdict'], 'KEY')

    def test_existing_roles_are_untouched_by_adding_one(self):
        for name, context in CONTEXTS.items():
            self.assertEqual(instruction_for(context), GOLDEN[name]['instruction'], name)


class PublicViewTests(unittest.TestCase):
    def test_the_public_metadata_never_contains_prompts_or_output_schemas(self):
        # /api/roles 返回它：给前端和指挥者看名字、描述、权限，不泄漏提示词全文。 It backs /api/roles: names, descriptions, permissions; never the prompt text.
        for item in registry.current().public():
            self.assertEqual(set(item), {'id', 'label', 'description', 'order', 'level', 'routable', 'permissions', 'legacy', 'when'})
        self.assertNotIn('Return exactly one JSON object', json.dumps(registry.current().public(), ensure_ascii=False))

    def test_the_order_is_by_the_order_field_so_the_ui_can_rely_on_it(self):
        orders = [item['order'] for item in registry.current().public()]
        self.assertEqual(orders, sorted(orders))


class JsonSchemaTests(unittest.TestCase):
    def test_types_enums_bounds_and_nesting(self):
        schema = {'type': 'object', 'required': ['a'], 'additionalProperties': False,
                  'properties': {'a': {'type': 'integer', 'minimum': 1, 'maximum': 3}, 'b': {'type': 'array', 'items': {'enum': ['x', 'y']}, 'minItems': 1, 'maxItems': 2}}}
        validate({'a': 2, 'b': ['x']}, schema)
        for bad in ({'a': True}, {'a': 0}, {'a': 4}, {'a': 1, 'b': []}, {'a': 1, 'b': ['z']}, {'a': 1, 'b': ['x', 'x', 'y']}, {'a': 1, 'c': 1}, {}):
            with self.subTest(bad=str(bad)), self.assertRaises(SchemaError):
                validate(bad, schema)

    def test_one_of_const_and_the_path_in_the_message(self):
        schema = {'oneOf': [{'type': 'object', 'required': ['kind'], 'properties': {'kind': {'const': 'a'}}}, {'type': 'string'}]}
        validate({'kind': 'a'}, schema)
        validate('text', schema)
        with self.assertRaises(SchemaError):
            validate(5, schema)
        with self.assertRaisesRegex(SchemaError, r'\$\.items\[1\]'):
            validate({'items': ['ok', 5]}, {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'string'}}}})


if __name__ == '__main__':
    unittest.main()
