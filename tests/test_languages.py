"""语言包管理器与一致性检查（对注册表里的每个包参数化）。 The pack manager and the conformance checks, parametrised over every registered pack."""
import json
import sys
import types
import tempfile
import unittest
from pathlib import Path

from masa.domain.models import MasaError
from masa.languages.base import LanguagePack, load_manifest
from masa.languages.registry import Registry, registry

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def test_go_is_the_default_and_builtin(self):
        self.assertIn('go', registry.ids())
        self.assertEqual(registry.get().id, 'go')
        self.assertEqual(registry.for_run({}).id, 'go')  # 旧数据没有 language 字段，视为 go / old data has no language field
        self.assertEqual(registry.for_run({'language': 'go'}).id, 'go')

    def test_an_unknown_language_is_refused_with_a_clear_error(self):
        with self.assertRaisesRegex(MasaError, 'not available'):
            registry.get('cobol')

    def test_operation_ids_are_the_legacy_go_ids(self):
        go = registry.get('go')
        self.assertEqual({k: o.id for k, o in go.operations.items()},
                         {'test': 'go_test', 'static': 'go_vet', 'format': 'go_fmt_check', 'run': 'app_run', 'index': 'go_index'})
        self.assertEqual(registry.pack_of_operation('go_vet').id, 'go')
        self.assertEqual(go.kind_of('go_fmt_check'), 'format')

    def test_a_broken_external_pack_is_reported_without_affecting_others(self):
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / 'broken'
            bad.mkdir()
            (bad / 'pack.json').write_text(json.dumps({'id': 'broken', 'api_version': 99, 'layout': {}, 'operations': {}}), encoding='utf-8')
            reg = Registry()
            reg.configure(extra_dirs=[temp])
            reg.load(force=True)
            self.assertIn('go', reg.ids())
            self.assertNotIn('broken', reg.ids())
            self.assertIn('api_version', reg.errors()['broken'])

    def test_a_minimal_external_pack_loads_and_gets_its_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'toy'
            folder.mkdir()
            (folder / 'pack.json').write_text(json.dumps({
                'id': 'toy', 'api_version': 1,
                'layout': {'source_exts': ['.toy'], 'root_marker': 'toy.json', 'entrypoint': 'main.toy', 'test_suffixes': ['_test.toy']},
                'operations': {'test': {'id': 'toy_test', 'label': 'toy test'}}}), encoding='utf-8')
            (folder / 'pack.py').write_text('from masa.languages.base import LanguagePack\ndef create(manifest, root):\n    return LanguagePack(manifest=manifest, root=root)\n', encoding='utf-8')
            reg = Registry()
            reg.configure(configs={'toy': {'x': 1}}, extra_dirs=[temp])
            reg.load(force=True)
            toy = reg.get('toy')
            self.assertEqual(toy.config, {'x': 1})
            self.assertTrue(toy.is_test('a_test.toy') and toy.is_source('a.toy') and not toy.is_source('a_test.toy'))
            self.assertEqual(reg.pack_of_operation('toy_test').id, 'toy')

    def test_the_manifest_loader_rejects_incomplete_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pack.json'
            for bad in ({}, {'id': 'x', 'api_version': 1, 'layout': {}, 'operations': {}},
                        {'id': 'x', 'api_version': 1, 'layout': {'source_exts': [], 'root_marker': 'a', 'entrypoint': 'b', 'test_suffixes': []}, 'operations': {'build': {'id': 'b'}}}):
                path.write_text(json.dumps(bad), encoding='utf-8')
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_manifest(path)


class SettingsApiTests(unittest.TestCase):
    """设置 → 语言：默认语言与每个包的配置被校验、保存，并影响注册表。 Settings -> Languages: the default and each pack's settings are validated, saved and applied to the registry."""

    def setUp(self):
        from masa.application.console import Console
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.console = Console(Path(self.temp.name), Path(self.temp.name) / 'no-runner.exe', Path(self.temp.name) / 'no-go.exe', ROOT)
        self.addCleanup(lambda: registry.configure(default='go', configs={}, extra_dirs=[]))

    def test_the_language_list_carries_readiness_and_config_fields(self):
        info = {l['id']: l for l in self.console.languages()['languages']}
        self.assertIn('python', info)
        self.assertEqual({f['key'] for f in info['python']['config']}, {'interpreter', 'test_framework', 'package_policy', 'env_max_mb'})
        self.assertFalse(info['go']['readiness']['ok'])  # 这个临时环境没有 Go 工具链 / this temp setup has no Go toolchain
        self.assertEqual(self.console.bootstrap()['language'], 'go')
        self.assertEqual({l['id'] for l in self.console.bootstrap()['languages']} >= {'go', 'python'}, True)

    def test_saving_validates_and_applies_the_configuration(self):
        self.console.save_languages({'default': 'python', 'packs': {'python': {'test_framework': 'pytest', 'interpreter': ' ' + sys.executable + ' '}}})
        python = registry.get('python')
        self.assertEqual(registry.default_id(), 'python')
        self.assertEqual(python.framework(), 'pytest')
        self.assertEqual(python.interpreter(), Path(sys.executable))  # 首尾空白被去掉 / surrounding spaces are stripped
        self.assertIn('pytest', python.operations['test'].args[-5:] + python.operations['test'].args)
        saved = json.loads((Path(self.temp.name) / 'languages.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['default'], 'python')
        for bad in ({'default': 'cobol'}, {'packs': {'python': {'nope': 1}}}, {'packs': {'python': {'test_framework': 'jest'}}}, {'packs': {'cobol': {}}}, {'extra_dirs': 'x'}):
            with self.subTest(bad=bad), self.assertRaises(MasaError):
                self.console.save_languages(bad)
        # 重新读取：新的 Console 启动时应用保存的配置 / a new Console applies the saved configuration at start
        from masa.application.console import Console
        registry.configure(default='go', configs={}, extra_dirs=[])
        Console(Path(self.temp.name), Path(self.temp.name) / 'no-runner.exe', Path(self.temp.name) / 'no-go.exe', ROOT)
        self.assertEqual(registry.default_id(), 'python')

    def test_triage_follows_the_chosen_language(self):
        goal = '写一个 Python 脚本统计文件行数'
        self.assertEqual(self.console.triage({'goal': goal, 'language': 'go'})['verdict'], 'infeasible')
        self.assertNotEqual(self.console.triage({'goal': goal, 'language': 'python'})['verdict'], 'infeasible')

    def test_a_task_in_a_language_that_is_not_ready_is_refused_when_a_runner_exists(self):
        runner = Path(self.temp.name) / 'masa-runner.exe'
        runner.write_bytes(b'x')
        self.console.runner_path = runner
        with self.assertRaisesRegex(MasaError, 'not ready'):
            self.console._language_of({'language': 'go'})  # 这里没有 Go 工具链 / there is no Go toolchain here
        self.assertEqual(self.console._language_of({'language': 'python'}), 'python')


class ConformanceTests(unittest.TestCase):
    """每个包都必须满足的最低要求。 What every pack must satisfy."""
    def packs(self):
        return [registry.get(i) for i in registry.ids()]

    def test_every_pack_is_a_language_pack_with_consistent_layout(self):
        for pack in self.packs():
            with self.subTest(pack=pack.id):
                self.assertIsInstance(pack, LanguagePack)
                entry = pack.entrypoint()
                self.assertTrue(pack.is_code(entry) and pack.is_source(entry) and not pack.is_test(entry))
                self.assertTrue(pack.operations['test'].id)
                ids = [op.id for op in pack.operations.values()]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(pack.root_marker)

    def test_operation_ids_are_unique_across_packs(self):
        seen = {}
        for pack in self.packs():
            for op in pack.operations.values():
                self.assertNotIn(op.id, seen, f'{op.id} used by {seen.get(op.id)} and {pack.id}')
                seen[op.id] = pack.id

    def test_the_conformance_check_passes_for_every_pack_and_catches_a_broken_one(self):
        from masa.languages import conformance
        probe = types.SimpleNamespace()
        for pack in self.packs():
            with self.subTest(pack=pack.id):
                failed = [name for name, ok, _ in conformance.check(pack, probe) if not ok]
                self.assertEqual(failed, [])
        class Broken(LanguagePack):
            pass
        broken = Broken(manifest={'id': 'broken', 'layout': {'source_exts': ['.x'], 'root_marker': 'x.json', 'entrypoint': 'main.x', 'test_suffixes': ['_t.x']},
                                  'operations': {'test': {'id': 'go_test'}}})  # 操作 id 与 Go 冲突 / the id collides with Go's
        names = [name for name, ok, _ in conformance.check(broken, probe) if not ok]
        self.assertIn('operations: ids do not collide with another pack', names)

    def test_the_lang_command_lists_and_checks(self):
        import contextlib
        import io
        from masa.interfaces.cli import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['lang', 'list']), 0)
            self.assertEqual(main(['lang', 'check', 'python']), 0)
        self.assertIn('python', out.getvalue())
        self.assertIn('ok   operations: ids are unique', out.getvalue())

    def test_describe_is_json_serialisable(self):
        json.dumps(registry.describe())


if __name__ == '__main__':
    unittest.main()
