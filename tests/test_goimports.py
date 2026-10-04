"""确定性 import 修复。Deterministic import fixing — cases taken from real weak-model output."""
import unittest

from masa.application.goimports import fix_imports, fix_module_imports

NL = chr(10)


def src(*lines):
    return NL.join(lines) + NL


class FixImportsTests(unittest.TestCase):
    def test_a_missing_stdlib_import_is_added_to_the_existing_block(self):
        # 真实错误：undefined: io / undefined: bytes
        source = src('package wc', '', 'import (', '\t"bufio"', ')', '', 'func Count(r io.Reader) {', '\t_ = bufio.NewScanner(r)', '\tvar b bytes.Buffer', '\t_ = b', '}')
        fixed, changes = fix_imports(source)
        self.assertEqual(sorted(changes), [('add', 'bytes'), ('add', 'io')])
        self.assertIn('\t"io"', fixed)
        self.assertIn('\t"bytes"', fixed)
        self.assertIn('\t"bufio"', fixed)

    def test_an_unused_import_is_removed(self):
        # 真实错误："bufio" imported and not used
        source = src('package main', '', 'import (', '\t"bufio"', '\t"fmt"', ')', '', 'func main() {', '\tfmt.Println("x")', '}')
        fixed, changes = fix_imports(source)
        self.assertEqual(changes, [('remove', 'bufio')])
        self.assertNotIn('bufio', fixed)
        self.assertIn('"fmt"', fixed)

    def test_a_block_that_becomes_empty_disappears_and_missing_imports_create_one(self):
        fixed, changes = fix_imports(src('package a', '', 'import (', '\t"strings"', ')', '', 'func F() { fmt.Println(1) }'))
        self.assertEqual(sorted(changes), [('add', 'fmt'), ('remove', 'strings')])
        self.assertIn('\t"fmt"', fixed)
        self.assertNotIn('strings', fixed)

    def test_a_source_without_imports_gets_a_block_after_the_package_clause(self):
        fixed, changes = fix_imports(src('package a', '', 'func F() { os.Exit(1) }'))
        self.assertEqual(changes, [('add', 'os')])
        self.assertTrue(fixed.startswith('package a' + NL + NL + 'import (' + NL + '\t"os"' + NL + ')'))

    def test_a_single_line_import_becomes_a_block_when_something_is_added(self):
        fixed, _ = fix_imports(src('package a', '', 'import "fmt"', '', 'func F() { fmt.Println(strings.ToUpper("a")) }'))
        self.assertIn('import (' + NL + '\t"fmt"' + NL + '\t"strings"' + NL + ')', fixed)

    def test_a_local_variable_named_like_a_package_is_never_imported(self):
        # 真实情况：bytes := 0 遮蔽了包名；这时 bytes.Buffer 之外的裸用法说明它是变量。
        source = src('package a', '', 'func F(bytes int) int {', '\tbytes += 1', '\treturn bytes', '}')
        self.assertEqual(fix_imports(source), (source, []))
        mixed = src('package a', '', 'func F() {', '\tbytes := 3', '\t_ = bytes', '\tvar b bytes.Buffer', '\t_ = b', '}')
        self.assertEqual(fix_imports(mixed)[1], [])  # 同时出现两种用法：不猜 / both uses present: do not guess

    def test_comments_and_strings_do_not_count_as_usage_or_as_declarations(self):
        source = src('package a', '', 'import "os"', '', '// os.Exit is not called here', 'func F() string { return "os.Exit(1) and io.EOF" }')
        fixed, changes = fix_imports(source)
        self.assertEqual(changes, [('remove', 'os')])
        self.assertNotIn('"os"', fixed)

    def test_aliased_dot_blank_and_third_party_imports_are_left_alone(self):
        source = src('package a', '', 'import (', '\tr "crypto/rand"', '\t_ "embed"', '\t"github.com/x/y"', ')', '', 'func F() {}')
        self.assertEqual(fix_imports(source), (source, []))

    def test_ambiguous_names_and_nonsense_input_are_untouched(self):
        self.assertEqual(fix_imports(src('package a', '', 'func F() { rand.Intn(3) }'))[1], [])  # rand 有两个包 / two packages
        self.assertEqual(fix_imports('not go at all'), ('not go at all', []))
        clean = src('package a', '', 'import "fmt"', '', 'func F() { fmt.Println(1) }')
        self.assertEqual(fix_imports(clean), (clean, []))

    def test_struct_fields_and_method_chains_are_not_mistaken_for_packages(self):
        source = src('package a', '', 'func F(s S) int {', '\treturn s.count + s.inner.size', '}')
        self.assertEqual(fix_imports(source)[1], [])


class FixModuleImportsTests(unittest.TestCase):
    DIRS = ['internal/expense']

    def test_a_wrongly_prefixed_internal_import_is_rewritten_to_the_approved_module(self):
        # 真实任务：弱模型把 module 前缀编成了 github.com/example.com/expense
        source = src('package main', '', 'import (', '	"fmt"', '	"github.com/example.com/expense/internal/expense"', ')', '', 'func main() { fmt.Println(expense.X) }')
        fixed, changes = fix_module_imports(source, 'example.com/task', self.DIRS)
        self.assertIn('"example.com/task/internal/expense"', fixed)
        self.assertNotIn('github.com', fixed)
        self.assertEqual(changes[0][0], 'module')

    def test_correct_standard_unrelated_and_ambiguous_imports_are_left_alone(self):
        ok = src('package a', '', 'import (', '	"fmt"', '	"example.com/task/internal/expense"', '	"github.com/other/lib"', ')')
        self.assertEqual(fix_module_imports(ok, 'example.com/task', self.DIRS), (ok, []))
        two = src('package a', '', 'import "github.com/x/internal/expense"')
        self.assertEqual(fix_module_imports(two, 'example.com/task', ['internal/expense', 'expense'])[1], [])  # 两个目录都匹配：不猜 / ambiguous
        self.assertEqual(fix_module_imports(two, None, self.DIRS), (two, []))


if __name__ == '__main__':
    unittest.main()
