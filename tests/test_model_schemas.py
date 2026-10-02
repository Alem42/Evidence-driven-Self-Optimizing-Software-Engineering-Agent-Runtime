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
        refs=tester['properties']['checks']['items']['properties']['acceptance_indices']['items']
        self.assertEqual((refs['minimum'],refs['maximum']),(0,1))

    def test_developer_paths_match_approved_spec_and_tools_keep_json(self):
        """生成器只能返回批准路径；工具协议保持原样。 Restrict generated paths without changing tool contracts."""
        schema=response_schema({'purpose':'project_developer','spec':{'files':[{'path':'go.mod'},{'path':'cmd/app/main.go'}]}})
        files=schema['properties']['files']
        self.assertEqual(files['required'],['go.mod','cmd/app/main.go'])
        self.assertFalse(files['additionalProperties'])
        self.assertEqual(response_schema({'purpose':'verifier'}),'json')
