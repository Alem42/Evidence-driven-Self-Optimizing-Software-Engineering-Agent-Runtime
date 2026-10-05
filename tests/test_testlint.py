"""冻结前的测试静态检查。Static pre-freeze test checks."""
import unittest

from masa.application.testlint import test_problem_messages, test_problems

NL = chr(10)


def go(*lines):
    return NL.join(lines) + NL


GOOD_TABLE = go('package wc', '', 'import "testing"', '', 'func TestCount(t *testing.T) {', '\tfor _, tt := range cases {', '\t\tt.Run(tt.name, func(t *testing.T) {',
                '\t\t\tif got := Count(tt.in); got != tt.want {', '\t\t\t\tt.Errorf("Count(%q) = %d, want %d", tt.in, got, tt.want)', '\t\t\t}', '\t\t})', '\t}', '}')


class TestLintTests(unittest.TestCase):
    def test_a_normal_table_driven_test_passes(self):
        self.assertEqual(test_problems(GOOD_TABLE), [])

    def test_a_test_that_can_never_fail_is_rejected(self):
        vacuous = go('package a', '', 'import "testing"', '', 'func TestX(t *testing.T) {', '\tt.Log("ok")', '}')
        self.assertTrue(any('no test ever fails' in p for p in test_problems(vacuous)))

    def test_a_file_without_test_functions_and_a_skipped_test_are_rejected(self):
        self.assertTrue(any('no Test' in p for p in test_problems(go('package a', '', 'func helper() {}'))))
        skipped = go('package a', '', 'import "testing"', '', 'func TestX(t *testing.T) {', '\tt.Skip("later")', '\tt.Fatal("x")', '}')
        self.assertTrue(any('skipped' in p for p in test_problems(skipped)))

    def test_assertions_inside_comments_or_strings_do_not_count(self):
        fake = go('package a', '', 'import "testing"', '', 'func TestX(t *testing.T) {', '\t// t.Fatal("never")', '\ts := "t.Errorf(x)"', '\t_ = s', '}')
        self.assertTrue(test_problems(fake))

    def test_helper_assertions_and_assert_libraries_count(self):
        helper = go('package a', '', 'import "testing"', '', 'func check(t *testing.T, ok bool) {', '\tif !ok {', '\t\tt.Fatalf("bad")', '\t}', '}', '',
                    'func TestX(t *testing.T) { check(t, true) }')
        self.assertEqual(test_problems(helper), [])

    def test_messages_cover_only_test_files(self):
        files = {'a.go': 'package a', 'a_test.go': go('package a', 'func helper() {}')}
        self.assertEqual(list(test_problem_messages(files)), ['a_test.go'])


if __name__ == '__main__':
    unittest.main()
