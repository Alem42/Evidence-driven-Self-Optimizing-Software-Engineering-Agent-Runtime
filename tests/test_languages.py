"""语言包管理器与一致性检查（对注册表里的每个包参数化）。 The pack manager and the conformance checks, parametrised over every registered pack."""
import json
import tempfile
import unittest
from pathlib import Path

from masa.domain.models import MasaError
from masa.languages.base import LanguagePack, load_manifest
from masa.languages.registry import Registry, registry


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

    def test_describe_is_json_serialisable(self):
        json.dumps(registry.describe())


if __name__ == '__main__':
    unittest.main()
