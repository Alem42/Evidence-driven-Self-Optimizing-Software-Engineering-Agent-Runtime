"""本地结构输出与业务契约对齐。 Local structured outputs remain aligned with business contracts."""
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

