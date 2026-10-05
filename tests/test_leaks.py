"""提示词泄漏检查。Prompt-leak check — the sentence below is copied from a real failed task."""
import unittest

from masa.application.generation import ProjectGeneration
from masa.application.leaks import leak_messages, prompt_leaks
from masa.domain.models import MasaError

# 真实任务（stats_test.go:51）里被弱模型写进字符串的文字。
REAL = 't.Fatalf("Calculate() returned an error: % previous_attempt_error: The file internal/stats/stats_test.go could not be processed because it failed to compile.")'


class LeakTests(unittest.TestCase):
    def test_the_real_leaked_sentence_is_detected(self):
        self.assertEqual(prompt_leaks(REAL), ['previous_attempt_error'])

    def test_clean_code_and_ordinary_words_are_not_flagged(self):
        clean = 'package a\n\n// original value of the file\nfunc F(previous int, target string) int { return previous }\n'
        self.assertEqual(prompt_leaks(clean), [])
        self.assertEqual(prompt_leaks(None), [])

    def test_messages_cover_only_go_files_and_name_the_fragment(self):
        files = {'a.go': REAL, 'go.mod': 'previous_attempt_error', 'b.go': 'package b\n'}
        found = leak_messages(files)
        self.assertEqual(list(found), ['a.go'])
        self.assertIn('previous_attempt_error', found['a.go'])
        self.assertEqual(leak_messages(files, ['b.go']), {})

    def test_a_repair_or_test_revision_that_leaks_is_rejected_so_it_is_retried_with_the_reason(self):
        with self.assertRaisesRegex(MasaError, 'harness prompt text'):
            ProjectGeneration._refuse_leaks({'internal/a/a_test.go': REAL})
        ProjectGeneration._refuse_leaks({'internal/a/a.go': 'package a' + chr(10)})  # 干净的不报错 / clean passes


if __name__ == '__main__':
    unittest.main()
