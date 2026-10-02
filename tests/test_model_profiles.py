"""多模型配置与无密钥本地协议边界。 Multi-model metadata and keyless local protocol boundaries."""
import tempfile
import unittest
from pathlib import Path
from masa.infrastructure.llm import ChatProvider,validate_config
from masa.infrastructure.settings import Settings
from masa.domain.models import MasaError
from masa.agents.protocol import validate_response


class ModelProfileTests(unittest.TestCase):
    def test_legacy_cloud_profile_identity_and_secret_requirement(self):
        provider=ChatProvider({'base_url':'https://example.com/v1','model':'m'},'synthetic')
        self.assertEqual(provider.config['model_type'],'cloud')
        self.assertEqual(provider.config['level'],2)
        self.assertNotIn('level',provider.profile)
        self.assertEqual(provider.profile['provider'],'openai-compatible')
        with self.assertRaises(MasaError):ChatProvider({'base_url':'https://example.com/v1','model':'m'})

    def test_local_profiles_persist_without_keys_and_disabled_profiles_cannot_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp))
            result=settings.save({'new':True,'model_type':'local','base_url':'http://127.0.0.1:11434','model':'local-model','timeout_seconds':180})
            ident=result['active_id'];self.assertTrue(result['profiles'][0]['ready'])
            self.assertFalse(result['profiles'][0]['key_configured'])
            restored=Settings(Path(tmp));self.assertEqual(restored.provider(ident).profile['provider'],'ollama-native')
            restored.save({'id':ident,'enabled':False})
            with self.assertRaises(MasaError):restored.provider(ident)

    def test_metadata_rejects_unsafe_or_ambiguous_values(self):
        for invalid in ({'model_type':'local','base_url':'https://remote.example'}, {'level':True},{'enabled':'false'},
                        {'roles':['project_planner','project_planner']},{'roles':['shell']},{'input_price_per_million':float('nan')},
                        {'output_price_per_million':-1},{'context_limit':0},{'protocol':'ollama','model_type':'cloud'}):
            with self.subTest(invalid=invalid),self.assertRaises(MasaError):validate_config(invalid)

    def test_keyless_responses_are_not_corrupted_by_empty_secret_redaction(self):
        self.assertEqual(validate_response({'operation':'go_test'},{'type':'final','summary':'valid'},'')['summary'],'valid')
        provider=ChatProvider({'model_type':'local','base_url':'http://localhost:11434','model':'m','roles':['project_planner']})
        with self.assertRaisesRegex(MasaError,'does not allow role'):provider.respond({'purpose':'project_developer'})
