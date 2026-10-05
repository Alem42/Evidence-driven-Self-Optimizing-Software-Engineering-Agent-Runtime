"""失败证据的源码片段与尝试摘要。Source snippets in failure evidence and the attempt summary."""
import unittest

from masa.application import attempts
from masa.application.generation import concise_failure_evidence
from masa.application.snippets import source_snippets

SOURCE = '\n'.join(f'line{n}' for n in range(1, 31))
FILES = {'internal/wc/wc.go': SOURCE, 'cmd/app/main.go': 'package main\n', 'internal/wc/wc_test.go': 'package wc\n'}


class SnippetTests(unittest.TestCase):
    def test_a_diagnostic_gets_numbered_source_around_it_and_the_failing_line_is_marked(self):
        found = source_snippets(['internal/wc/wc.go:10:3: undefined: io'], FILES, radius=2)
        self.assertEqual(len(found), 1)
        lines = found[0]['source'].split('\n')
        self.assertEqual(len(lines), 5)  # 8..12
        self.assertTrue(lines[2].startswith('>>') and 'line10' in lines[2])
        self.assertEqual((found[0]['path'], found[0]['line']), ('internal/wc/wc.go', 10))

    def test_windows_backslash_paths_and_package_relative_names_resolve_when_unique(self):
        self.assertEqual(source_snippets(['internal\\wc\\wc.go:5:1: x'], FILES)[0]['path'], 'internal/wc/wc.go')
        self.assertEqual(source_snippets(['wc_test.go:1:1: x'], FILES)[0]['path'], 'internal/wc/wc_test.go')  # go test 输出只带文件名 / bare name from go test

    def test_ambiguous_or_unknown_paths_and_out_of_range_lines_are_skipped(self):
        files = {'a/x.go': 'package a\n', 'b/x.go': 'package b\n'}
        self.assertEqual(source_snippets(['x.go:1:1: boom'], files), [])  # 两个文件同名：不猜 / two files share the name: do not guess
        self.assertEqual(source_snippets(['nope.go:1:1: boom'], FILES), [])
        self.assertEqual(source_snippets(['internal/wc/wc.go:999:1: boom'], FILES), [])
        self.assertEqual(source_snippets(['internal/wc/wc.go:3:1: a', 'internal/wc/wc.go:3:9: dup'], FILES)[0]['line'], 3)
        self.assertEqual(len(source_snippets(['internal/wc/wc.go:3:1: a', 'internal/wc/wc.go:3:9: dup'], FILES)), 1)

    def test_the_cap_and_the_evidence_integration(self):
        many = [f'internal/wc/wc.go:{n}:1: e' for n in range(1, 30)]
        self.assertEqual(len(source_snippets(many, FILES, limit=8)), 8)
        result = {'stdout': '', 'stderr': 'internal\\wc\\wc.go:7:2: undefined: bytes\n', 'exit_code': 1}
        evidence = concise_failure_evidence(result, FILES)
        self.assertEqual(evidence['snippets'][0]['line'], 7)
        self.assertEqual(concise_failure_evidence(result)['snippets'], [])  # 没有源码时不报错 / no source, no failure


class AttemptSummaryTests(unittest.TestCase):
    def analysis(self, *messages):
        return {'items': [{'message': m} for m in messages]}

    def test_each_round_records_before_after_and_what_remains(self):
        job = {'fix_log': [{'stage': 'repair', 'changed': ['internal/wc/wc.go'], 'level': 1, 'model': 'glm', 'before': 7}]}
        attempts.close_attempt(job, self.analysis('a', 'b', 'c'))
        self.assertEqual((job['fix_log'][0]['after'], job['unresolved_now']), (3, 3))
        text = attempts.summary(job)
        self.assertIn('round 1 (repair, by glm) changed internal/wc/wc.go: unresolved 7 -> 3 (improved)', text)
        self.assertIn('still failing: a | b | c', text)

    def test_a_round_that_did_not_help_is_labelled_so(self):
        job = {'fix_log': [{'stage': 'repair', 'changed': [], 'level': 2, 'model': None, 'before': 2}]}
        attempts.close_attempt(job, self.analysis('x', 'y'))
        self.assertIn('(no improvement)', attempts.summary(job))
        self.assertIn('changed nothing', attempts.summary(job).replace('changed nothing', 'changed nothing'))

    def test_an_unfinished_round_is_not_closed_twice_and_nothing_yields_empty_text(self):
        job = {'fix_log': [{'stage': 'repair', 'changed': ['a.go'], 'level': 1, 'model': 'm', 'before': 4}]}
        attempts.close_attempt(job, self.analysis('x'))
        attempts.close_attempt(job, self.analysis('x', 'y', 'z'))  # 第二次验证属于下一次修复 / belongs to the NEXT fix
        self.assertEqual(job['fix_log'][0]['after'], 1)
        self.assertEqual(attempts.summary({}), '')
        self.assertEqual(attempts.summary({'fix_log': [{'stage': 'repair'}]}), '')

    def test_only_the_latest_rounds_are_kept(self):
        log = [{'stage': 'repair', 'changed': [f'{n}.go'], 'level': 1, 'model': 'm', 'before': n + 1, 'after': n} for n in range(1, 8)]
        text = attempts.summary({'fix_log': log}, keep=3)
        self.assertEqual(text.count('- round'), 3)
        self.assertIn('round 7', text)
        self.assertNotIn('round 3 ', text)


if __name__ == '__main__':
    unittest.main()
