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
    """工人角色每步只执行第一个动作；其它角色对多余内容仍然严格。 A worker role executes the first action only; other roles stay strict about trailing content."""

    def test_the_first_action_is_used_and_the_junk_after_it_is_ignored_for_workers_only(self):
        from masa.infrastructure.llm import decode_action
        text = '{"tool":"write_file","path":"a.py","content":"x"}' + chr(10) + chr(10) + '{"tool":"run_check","kind":"test"}'
        self.assertEqual(decode_action(text, 'package_worker')['tool'], 'write_file')
        self.assertEqual(decode_action('{"tool":"finish"}</tags>', 'package_worker'), {'tool': 'finish'})
        with self.assertRaises(json.JSONDecodeError):
            decode_action(text, 'project_planner')
        with self.assertRaises(json.JSONDecodeError):
            decode_action('{"tool":', 'package_worker')  # 不完整的第一个对象不是“多余内容” / an incomplete first object is not "trailing junk"
        self.assertEqual(decode_action('{"a":1}', 'project_planner'), {'a': 1})
