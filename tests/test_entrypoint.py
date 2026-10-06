"""入口检查：main.go 里不许读输入。Entrypoint check: main.go may not read input. The two mains below are copied from real benchmark output."""
import unittest

from masa.application.checks.entrypoint import entry_problem_messages
from masa.application.generation import ProjectGeneration
from masa.domain.models import MasaError

NL = chr(10)

# 评测里 wc 的真实 main：往 nil 切片里 ReadFull，永远读到 0 字节，输出“0 0 0”，系统自己的测试却通过了。
WC_MAIN = NL.join(['package main', '', 'import (', '\t"fmt"', '\t"io"', '\t"os"', ')', '', 'func main() {', '\tvar input []byte', '',
                   '\t_, err := io.ReadFull(os.Stdin, input)', '\tif err != nil && err != io.EOF {', '\t\tos.Exit(1)', '\t}', '\tfmt.Println(len(input))', '}']) + NL
# 评测里 lru 的真实 main：只读第一行，容量写死成 1。
LRU_MAIN = NL.join(['package main', '', 'import (', '\t"bufio"', '\t"os"', ')', '', 'func main() {', '\tscanner := bufio.NewScanner(os.Stdin)', '\tif scanner.Scan() {', '\t}', '}']) + NL
THIN = NL.join(['package main', '', 'import (', '\t"os"', '', '\t"example.com/task/internal/wc"', ')', '', 'func main() {',
                '\tos.Exit(wc.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))', '}']) + NL


class EntrypointTests(unittest.TestCase):
    def test_the_real_wiring_bugs_are_flagged(self):
        for name, source in (('wc', WC_MAIN), ('lru', LRU_MAIN)):
            with self.subTest(name):
                found = entry_problem_messages({'cmd/app/main.go': source})
                self.assertIn('cmd/app/main.go', found)
                self.assertIn('Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)', found['cmd/app/main.go'])  # 给出具体写法 / gives the concrete shape

    def test_a_thin_wrapper_and_a_plain_hello_world_pass(self):
        self.assertEqual(entry_problem_messages({'cmd/app/main.go': THIN}), {})
        hello = 'package main' + NL + 'import "fmt"' + NL + 'func main() { fmt.Println("hello, masa") }' + NL
        self.assertEqual(entry_problem_messages({'cmd/app/main.go': hello}), {})

    def test_only_cmd_main_files_are_checked_and_comments_or_strings_do_not_count(self):
        self.assertEqual(entry_problem_messages({'internal/wc/wc.go': WC_MAIN}), {})  # 内部包里读输入是对的 / reading input in a package is right
        commented = 'package main' + NL + '// bufio.NewScanner(os.Stdin) is done in the package' + NL + 'func main() { println("io.ReadAll") }' + NL
        self.assertEqual(entry_problem_messages({'cmd/app/main.go': commented}), {})

    def test_a_repair_that_moves_input_reading_back_into_main_is_rejected(self):
        with self.assertRaisesRegex(MasaError, 'reads or parses input'):
            ProjectGeneration._refuse_leaks({'cmd/app/main.go': WC_MAIN})
        ProjectGeneration._refuse_leaks({'cmd/app/main.go': THIN})

    def test_a_repair_is_not_blocked_by_a_violation_that_was_already_there(self):
        # 评测里 lcs：原来的 main.go 就读 stdin，修复只要保留这个写法就被拒绝，整个任务失败。
        ProjectGeneration._refuse_leaks({'cmd/app/main.go': WC_MAIN}, {'cmd/app/main.go': WC_MAIN})  # 原样保留：放行 / kept as it was: allowed
        with self.assertRaisesRegex(MasaError, 'reads or parses input'):
            ProjectGeneration._refuse_leaks({'cmd/app/main.go': WC_MAIN}, {'cmd/app/main.go': THIN})  # 新引入：拒绝 / newly introduced: refused


if __name__ == '__main__':
    unittest.main()
