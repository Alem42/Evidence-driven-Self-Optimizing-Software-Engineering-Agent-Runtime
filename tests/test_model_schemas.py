"""本地结构输出与业务契约对齐。 Local structured outputs remain aligned with business contracts."""
import json
import unittest
from masa.agents.schemas import response_schema


class ModelSchemaTests(unittest.TestCase):
    def test_planner_clarification_and_tester_references_are_bounded(self):
        """澄清保持可用，验收引用按当前规格限制。 Preserve clarification and bind references to current specs."""
        spec=response_schema({'purpose':'project_planner'})
        self.assertEqual(set(spec['required']),{'summary','module','entrypoint','files','acceptance'})
        choices=response_schema({'purpose':'project_planner','clarification_allowed':True})['oneOf']
        self.assertEqual(len(choices),2)
        self.assertEqual(choices[1]['properties']['kind']['const'],'clarification_request')
        tester=response_schema({'purpose':'project_tester','spec':{'acceptance':['a','b']}})
        checks=tester['properties']['checks']
        refs=checks['properties']['go_test']['properties']['acceptance_indices']['items']
        self.assertEqual((refs['minimum'],refs['maximum']),(0,1))
        self.assertEqual(set(checks['properties']),{'go_test','go_vet','go_fmt_check'})
        self.assertFalse(checks['additionalProperties'])



class LenientTrailingTests(unittest.TestCase):
    """模型常在第一个 JSON 对象之后追加第二个动作或标记：取第一个完整对象；不完整的第一个对象仍然是错。 Models often append a second action or markup after the first JSON object: the first complete object is used; an incomplete first object is still an error."""

    def test_the_first_object_is_used_and_the_junk_after_it_is_ignored(self):
        from masa.infrastructure.llm import decode_action
        text = '{"tool":"write_file","path":"a.py","content":"x"}' + chr(10) + chr(10) + '{"tool":"run_check","kind":"test"}'
        self.assertEqual(decode_action(text, 'package_worker')['tool'], 'write_file')
        self.assertEqual(decode_action('{"tool":"finish"}</tags>', 'package_worker'), {'tool': 'finish'})
        self.assertEqual(decode_action('{"files":{"a.py":"x"}}' + chr(10) + 'Done.', 'project_repair'), {'files': {'a.py': 'x'}})
        with self.assertRaises(json.JSONDecodeError):
            decode_action('{"tool":', 'package_worker')  # 不完整的第一个对象不是“多余内容” / an incomplete first object is not "trailing junk"
        with self.assertRaises(json.JSONDecodeError):
            decode_action('not json at all', 'project_planner')
        self.assertEqual(decode_action('{"a":1}', 'project_planner'), {'a': 1})

    def test_raw_newlines_and_tabs_inside_strings_are_accepted(self):
        from masa.infrastructure.llm import decode_action
        raw = '{"files":{"a.py":"line one' + chr(10) + chr(9) + 'line two"}}'
        self.assertEqual(decode_action(raw, 'project_developer')['files']['a.py'], 'line one' + chr(10) + chr(9) + 'line two')
