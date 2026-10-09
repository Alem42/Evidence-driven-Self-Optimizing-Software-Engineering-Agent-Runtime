"""内部包导入检查：导入了规格里不存在的内部包要被抓到，合法导入、标准库、第三方不能误报。 Internal import check: catch imports of internal packages the spec lacks; no false alarms on valid, standard-library or third-party imports."""
import unittest

from masa.application.checks.imports import imports_of, package_dirs, unknown_import_messages

MODULE = 'example.com/bank'
PLANNED = ['go.mod', 'cmd/app/main.go', 'internal/parser/parser.go', 'internal/parser/parser_test.go', 'internal/store/store.go', 'internal/rules/rules.go']


class ImportsTests(unittest.TestCase):
    def test_imports_are_read_from_blocks_aliases_and_single_lines(self):
        source = 'package x\n\nimport "fmt"\nimport alias "example.com/bank/internal/store"\n\nimport (\n\t"os"\n\t// "example.com/bank/internal/commented"\n\t"example.com/bank/internal/parser"\n\tr "example.com/bank/internal/rules"\n)\n'
        self.assertEqual(sorted(imports_of(source)), ['example.com/bank/internal/parser', 'example.com/bank/internal/rules', 'example.com/bank/internal/store', 'fmt', 'os'])

    def test_a_missing_internal_package_is_reported_with_the_list_of_real_ones(self):
        files = {'cmd/app/main.go': 'package main\n\nimport (\n\t"os"\n\t"example.com/bank/internal/app"\n)\n\nfunc main() { app.Run(); os.Exit(0) }\n'}
        found = unknown_import_messages(files, ['cmd/app/main.go'], MODULE, PLANNED)
        self.assertIn('example.com/bank/internal/app', found['cmd/app/main.go'])
        for real in ('internal/parser', 'internal/store', 'internal/rules'):
            self.assertIn(f'{MODULE}/{real}', found['cmd/app/main.go'])
        self.assertNotIn(f'{MODULE}/cmd/app', found['cmd/app/main.go'])  # main 包不可导入，不列出 / the main package is not importable, so it is not listed

    def test_valid_standard_and_third_party_imports_are_never_reported(self):
        files = {'cmd/app/main.go': 'package main\n\nimport (\n\t"fmt"\n\t"strings"\n\t"example.com/bank/internal/parser"\n\t"example.com/bank/internal/rules"\n\t"golang.org/x/text/unicode/norm"\n)\n'}
        self.assertEqual(unknown_import_messages(files, ['cmd/app/main.go'], MODULE, PLANNED), {})

    def test_non_go_files_are_ignored_and_a_root_package_in_the_spec_is_importable(self):
        self.assertEqual(package_dirs(PLANNED), {'cmd/app', 'internal/parser', 'internal/store', 'internal/rules'})
        files = {'go.mod': 'module example.com/bank\n', 'README.md': 'import "example.com/bank/internal/ghost"'}
        self.assertEqual(unknown_import_messages(files, ['go.mod', 'README.md'], MODULE, PLANNED), {})
        self.assertEqual(unknown_import_messages({'a.go': 'package a\nimport "example.com/bank"\n'}, ['a.go'], MODULE, PLANNED + ['x.go']), {})


if __name__ == '__main__':
    unittest.main()
