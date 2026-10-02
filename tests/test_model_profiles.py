"""多模型配置与无密钥本地协议边界。 Multi-model metadata and keyless local protocol boundaries."""
import tempfile
import unittest
from pathlib import Path
from masa.infrastructure.llm import ChatProvider,validate_config
from masa.infrastructure.settings import Settings
from masa.domain.models import MasaError
from masa.agents.protocol import validate_response


class ModelProfileTests(unittest.TestCase):
    def test_snapshot_restores_options_without_moving_credentials(self):
        """冻结参数可恢复，删除/禁用/换地址不能带走凭据。 Restore frozen options without moving credentials."""
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp))
            result=settings.save({'new':True,'base_url':'https://example.com/v1','model':'old','api_key':'synthetic'})
            ident=result['active_id'];original=settings.provider(ident);snapshot=original.snapshot
            self.assertNotIn('api_key',snapshot['config'])
            settings.save({'id':ident,'model':'new','level':5,'max_output_tokens':4096})
            restored=settings.provider(snapshot=snapshot,expected=original.profile)
            self.assertEqual(restored.config['model'],'old')
            self.assertEqual(restored.config['level'],2)
            self.assertEqual(restored.key,'synthetic')
            settings.save({'id':ident,'base_url':'https://other.example/v1'})
            with self.assertRaisesRegex(MasaError,'destination unavailable'):settings.provider(snapshot=snapshot)

    def test_snapshot_respects_disable_delete_and_schema(self):
        """恢复不绕过显式禁用、删除或版本校验。 Recovery respects explicit disable/delete and schema checks."""
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp))
            result=settings.save({'new':True,'model_type':'local','base_url':'http://localhost:11434','model':'m'})
            ident=result['active_id'];snapshot=settings.provider(ident).snapshot
            settings.save({'id':ident,'enabled':False})
            with self.assertRaises(MasaError):settings.provider(snapshot=snapshot)
            settings.save({'action':'delete','id':ident})
            with self.assertRaises(MasaError):settings.provider(snapshot=snapshot)
            with self.assertRaises(MasaError):settings.provider(snapshot={'version':2})

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
