"""本地控制器的白名单、身份与真实HTTP入口。 Local controller allowlists, identities and HTTP entry points."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from masa.infrastructure.ollama import OllamaControl
from masa.application.console import Console
from masa.domain.models import MasaError


class OllamaControlTests(unittest.TestCase):
    def test_inventory_load_unload_and_details_use_fixed_endpoints(self):
        """动作只访问固定端点与安装模型。 Actions target fixed endpoints and installed models only."""
        control=OllamaControl()
        def request(path,body=None,timeout=10):
            return {'models':[{'name':'m:latest','size':100}]} if path=='/api/tags' else {'done':True,'capabilities':['completion'],'template':'private huge template'}
        with patch.object(control,'request',side_effect=request) as call:
            self.assertNotIn('template',control.show('m:latest'))
            control.residency('m:latest',False)
            self.assertEqual(call.call_args.args,('/api/generate',{'model':'m:latest','stream':False,'keep_alive':0}))
            with self.assertRaises(MasaError):control.residency('m; shell',True)

    def test_selection_reuses_profile_and_existing_tasks_block_mutation(self):
        """切换默认不复制配置，任务执行时不能释放模型。 Reuse profiles and forbid residency changes during project execution."""
        with tempfile.TemporaryDirectory() as temp:
            c=Console(Path(temp),'missing','missing',Path(temp))
            try:
                with patch.object(OllamaControl,'installed',return_value={'name':'m:latest','size':100}):
                    first=c.ollama_action({'action':'select','model':'m:latest'})['settings']['active_id']
                    second=c.ollama_action({'action':'select','model':'m:latest'})['settings']['active_id']
                    self.assertEqual(first,second)
                    self.assertEqual(c.settings.provider().config['model_type'],'local')
                    c.active='running'
                    with self.assertRaises(MasaError):c.ollama_action({'action':'unload','model':'m:latest'})
                    c.active=None
                    with self.assertRaises(MasaError):c.ollama_action({'action':'shell','model':'m:latest'})
            finally:c.close()
