"""评测系统：参考实现、独立判官、汇总、预算与停止。不调用任何模型。
Benchmark system: reference implementations, the independent oracle, aggregation, budgets and stopping. No model is called."""
import tempfile
import unittest
from pathlib import Path

from masa.bench import oracle
from masa.bench.report import FALSE_PASS, GATE_FAIL, PASS, SKIPPED, STOPPED, aggregate, compare, format_table
from masa.bench.runner import BenchRunner, list_results, load_result, resolve_config
from masa.bench.tasks import BY_ID, SUITES, TASKS, describe
from masa.domain.models import MasaError

GO = Path(__file__).resolve().parents[1] / '.tools' / 'go' / 'bin' / 'go.exe'
NL = chr(10)


def ref(task, args=(), stdin=''):
    return BY_ID[task].ref(list(args), stdin)


class ReferenceTests(unittest.TestCase):
    """期望输出来自参考实现，所以参考实现本身必须用手算的答案钉住。 The references define the expectations, so pin them with hand-computed answers."""

    def test_simple_levels(self):
        self.assertEqual(ref('hello'), ('hello, masa' + NL, 0))
        self.assertEqual(ref('upper', stdin='abc' + NL + 'Hello World' + NL), ('ABC' + NL + 'HELLO WORLD' + NL, 0))
        self.assertEqual(ref('join', ['a', 'b c']), ('a b c' + NL, 0))
        self.assertEqual(ref('join'), (NL, 0))
        self.assertEqual(ref('sum', stdin='1 2 3' + NL + '4' + NL), ('10' + NL, 0))
        self.assertEqual(ref('sum', stdin='1 x 2'), ('', 1))
        self.assertEqual(ref('sum', stdin=''), ('0' + NL, 0))
        self.assertEqual(ref('fizz', ['5']), (NL.join(['1', '2', 'Fizz', '4', 'Buzz']) + NL, 0))
        self.assertEqual(ref('fizz', ['0']), ('', 2))
        self.assertEqual(ref('fizz', ['abc']), ('', 2))

    def test_edge_semantics(self):
        self.assertEqual(ref('wc', stdin='a b' + NL + 'c'), ('1 3 5' + NL, 0))  # 没有结尾换行：行数是换行符个数 / no trailing newline
        self.assertEqual(ref('wc', stdin='你好' + NL), ('1 1 7' + NL, 0))  # 6 字节汉字 + 1 换行 / 6 bytes + newline
        self.assertEqual(ref('wc', stdin='  ' + NL + NL), ('2 0 4' + NL, 0))
        self.assertEqual(ref('wordfreq', stdin='the cat The dog the cat'), ('the 3' + NL + 'cat 2' + NL + 'dog 1' + NL, 0))
        self.assertEqual(ref('wordfreq', stdin='b a b a c'), ('a 2' + NL + 'b 2' + NL + 'c 1' + NL, 0))  # 并列按字典序 / tie by lexical order
        self.assertEqual(ref('roman', ['1994']), ('MCMXCIV' + NL, 0))
        self.assertEqual(ref('roman', ['MMXXIV']), ('2024' + NL, 0))
        for bad in (['IIII'], ['0'], ['4000'], ['abc'], []):
            self.assertEqual(ref('roman', bad), ('', 1), bad)

    def test_csv_sum_sorts_by_total_descending_then_name(self):
        out, code = ref('csv_sum', stdin='category,amount' + NL + 'food,10.5' + NL + 'rent,100' + NL + 'food,4.5' + NL)
        self.assertEqual((out, code), ('rent: 100.00' + NL + 'food: 15.00' + NL, 0))  # 这正是真实任务里测试写反的方向 / the direction a real test once got backwards
        self.assertEqual(ref('csv_sum', stdin='category,amount' + NL + 'a,1' + NL + 'b,1' + NL + 'c,2' + NL)[0], 'c: 2.00' + NL + 'a: 1.00' + NL + 'b: 1.00' + NL)
        self.assertEqual(ref('csv_sum', stdin='category,amount' + NL + 'food,abc' + NL + 'food,2' + NL)[0], 'food: 2.00' + NL)

    def test_dynamic_programming_and_graphs(self):
        self.assertEqual(ref('lcs', stdin='ABCBDAB' + NL + 'BDCABA' + NL)[0], '4' + NL)  # 教科书例子 / textbook example
        self.assertEqual(ref('lcs', stdin='abc' + NL)[0], '0' + NL)
        self.assertEqual(ref('knap', stdin=NL.join(['10', '5 10', '4 40', '6 30', '3 50']) + NL)[0], '90' + NL)  # 4+3 件，重量 7，价值 90 / items 2 and 4
        self.assertEqual(ref('maze', stdin=NL.join(['3 3', 'S..', '.#.', '..E']) + NL)[0], '4' + NL)
        self.assertEqual(ref('maze', stdin=NL.join(['3 3', 'S#.', '###', '..E']) + NL)[0], '-1' + NL)
        self.assertEqual(ref('topo', stdin=NL.join(['b c', 'a b', 'a d']) + NL)[0], NL.join(['a', 'b', 'c', 'd']) + NL)
        self.assertEqual(ref('topo', stdin='a b' + NL + 'b a' + NL)[0], 'CYCLE' + NL)
        self.assertEqual(ref('topo', stdin='x x' + NL)[0], 'CYCLE' + NL)

    def test_stateful_and_parsing(self):
        lru = NL.join(['2', 'put a 1', 'put b 2', 'get a', 'put c 3', 'get b', 'get a', 'get c']) + NL
        self.assertEqual(ref('lru', stdin=lru)[0], NL.join(['1', '-1', '1', '3']) + NL)
        calc = NL.join(['1+2*3', '(1+2)*3', '-7/2', '7/-2', '2*-3', '10/(5-5)', '1 +', '1 2', '--3']) + NL
        self.assertEqual(ref('calc', stdin=calc)[0], NL.join(['7', '9', '-3', '-3', '-6', 'error', 'error', 'error', '3']) + NL)
        kv = NL.join(['SET a 10', 'GET a', 'COUNT 10', 'BEGIN', 'SET a 20', 'BEGIN', 'DELETE a', 'GET a', 'ROLLBACK', 'GET a', 'COMMIT', 'GET a', 'ROLLBACK']) + NL
        self.assertEqual(ref('kvtx', stdin=kv)[0], NL.join(['10', '1', 'NULL', '20', '20', 'NO TRANSACTION']) + NL)
        rx = NL.join(['a*b\taaab', 'a*b\tb', 'a.c\tac', '(ab)+\taba', 'cat|dog\tcow']) + NL
        self.assertEqual(ref('regex', stdin=rx)[0], NL.join(['match', 'match', 'no match', 'no match', 'no match']) + NL)

    def test_the_library_is_well_formed(self):
        self.assertEqual(len({t.id for t in TASKS}), len(TASKS))
        self.assertEqual({t.level for t in TASKS}, set(range(10)))  # 10 个等级都有题 / every level has a task
        for task in TASKS:
            self.assertTrue(task.cases, task.id)
            for args, stdin in task.cases:
                out, code = task.ref(args, stdin)  # 每个用例都能算出期望 / every case yields an expectation
                self.assertIsInstance(out, str)
                self.assertIn(code, (0, 1, 2))
        for name, suite in SUITES.items():
            self.assertTrue(all(t in BY_ID for t in suite['tasks']), name)
        self.assertEqual(set(describe()['levels']), {str(i) for i in range(10)})

    def test_levels_get_progressively_larger_limits(self):
        self.assertLess(BY_ID['hello'].max_seconds, BY_ID['regex'].max_seconds)
        self.assertLess(BY_ID['hello'].max_cloud_tokens, BY_ID['regex'].max_cloud_tokens)


@unittest.skipUnless(GO.is_file(), 'Go toolchain not installed')
class OracleTests(unittest.TestCase):
    MOD = 'module example.com/task' + NL + NL + 'go 1.27.0' + NL

    def files(self, body):
        return {'go.mod': self.MOD, 'cmd/app/main.go': body}

    def test_a_correct_program_passes_and_a_wrong_one_is_caught(self):
        good = self.files('package main' + NL + 'import "fmt"' + NL + 'func main() { fmt.Println("hello, masa") }' + NL)
        self.assertTrue(oracle.judge(good, BY_ID['hello'], GO)['passed'])
        bad = self.files('package main' + NL + 'import "fmt"' + NL + 'func main() { fmt.Println("hello") }' + NL)
        verdict = oracle.judge(bad, BY_ID['hello'], GO)
        self.assertFalse(verdict['passed'])
        self.assertIn('stdout', verdict['cases'][0]['reason'])

    def test_exit_codes_and_stdin_are_checked(self):
        program = NL.join(['package main', 'import ("bufio"; "fmt"; "os"; "strconv"; "strings")', 'func main() {', '  s := bufio.NewScanner(os.Stdin); s.Buffer(make([]byte, 1<<20), 1<<20)', '  t := 0',
                           '  for s.Scan() { for _, w := range strings.Fields(s.Text()) {', '    n, err := strconv.Atoi(w)', '    if err != nil { fmt.Fprintln(os.Stderr, "error"); os.Exit(1) }', '    t += n } }',
                           '  fmt.Println(t) }']) + NL
        self.assertTrue(oracle.judge(self.files(program), BY_ID['sum'], GO)['passed'])
        wrong_exit = program.replace('os.Exit(1)', 'os.Exit(3)')
        verdict = oracle.judge(self.files(wrong_exit), BY_ID['sum'], GO)
        self.assertFalse(verdict['passed'])
        self.assertTrue(any('exit code 3' in c['reason'] for c in verdict['cases']))

    def test_a_program_that_does_not_build_is_reported_not_raised(self):
        verdict = oracle.judge(self.files('package main' + NL + 'func main() { undefined() }' + NL), BY_ID['hello'], GO)
        self.assertEqual((verdict['passed'], verdict['built']), (False, False))

    def test_trailing_whitespace_noise_is_tolerated_but_content_is_not(self):
        self.assertEqual(oracle.normalize('a  ' + NL + 'b' + NL + NL), 'a' + NL + 'b')
        self.assertNotEqual(oracle.normalize('a b'), oracle.normalize('a  b'))


def record(task, status, **over):
    base = {'task': task, 'level': BY_ID[task].level, 'repeat': 1, 'status': status, 'cloud_tokens': 1000, 'local_tokens': 500, 'seconds': 60, 'calls': 6, 'rounds': 2,
            'escalations': 1, 'mechanisms': {}}
    base.update(over)
    return base


class AggregateTests(unittest.TestCase):
    def test_pass_rates_levels_and_scores(self):
        records = [record('hello', PASS), record('hello', PASS), record('upper', PASS), record('upper', GATE_FAIL), record('wc', GATE_FAIL), record('wc', FALSE_PASS),
                   record('regex', SKIPPED)]
        agg = aggregate(records)
        o = agg['overall']
        self.assertEqual((o['runs'], o['skipped'], o['passed'], o['false_pass']), (6, 1, 3, 1))
        self.assertEqual(agg['by_level']['0']['rate'], 1.0)
        self.assertEqual(agg['by_level']['1']['rate'], 0.5)
        self.assertEqual(o['stable_level'], 0)  # L1 只有 50%，不稳定 / L1 is at 50%, not stable
        self.assertEqual(o['ceiling_level'], 1)
        # 加权：权重 = 等级+1；通过 hello×2(1+1) + upper×1(2) = 4，总权重 = 1+1+2+2+4+4 = 14
        self.assertEqual(o['weighted_score'], round(100 * 4 / 14, 1))
        self.assertEqual(o['cloud_tokens_per_pass'], 2000)  # 6000 token / 3 次通过 / 6000 tokens over 3 passes

    def test_mechanism_counts_and_empty_input(self):
        agg = aggregate([record('hello', PASS, mechanisms={'imports_fixed': 2, 'diagnosis': 1}), record('upper', PASS, mechanisms={'imports_fixed': 1})])
        self.assertEqual((agg['overall']['mechanisms']['imports_fixed'], agg['overall']['mechanisms']['diagnosis']), (3, 1))
        empty = aggregate([])
        self.assertEqual((empty['overall']['runs'], empty['overall']['rate'], empty['overall']['stable_level']), (0, None, None))

    def test_compare_reports_deltas_only_for_levels_present_in_both(self):
        base = aggregate([record('hello', PASS), record('wc', GATE_FAIL)])
        new = aggregate([record('hello', PASS), record('wc', PASS), record('regex', GATE_FAIL)])
        diff = compare(base, new)
        self.assertEqual(sorted(diff['levels']), ['0', '3'])
        self.assertEqual(diff['levels']['3']['delta'], 1.0)
        self.assertEqual(diff['overall']['rate']['base'], 0.5)

    def test_the_text_table_names_levels_and_symbols(self):
        text = format_table({'id': 'x', 'config': {'suite': 'canary'}, 'models': ['L1 a'], 'records': [record('hello', PASS), record('hello', STOPPED), record('upper', FALSE_PASS)]})
        self.assertIn('L0', text)
        self.assertIn('✓■', text)
        self.assertIn('≠', text)


class RunnerTests(unittest.TestCase):
    def build(self, run_task, **config):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = resolve_config({'suite': 'canary', **config})
        clock = [0.0]

        def tick(task, repeat, limits):
            clock[0] += 60
            self.limits.append(limits)
            return run_task(task, repeat, limits)

        self.limits = []
        runner = BenchRunner(Path(temp.name), '/x', '/y', Path(temp.name), cfg, run_task=tick, clock=lambda: clock[0])
        return runner, temp

    @staticmethod
    def passing(task, repeat, limits):
        return {'task': task.id, 'level': task.level, 'repeat': repeat + 1, 'status': PASS, 'cloud_tokens': 10_000, 'local_tokens': 0, 'seconds': 60, 'calls': 3, 'rounds': 1,
                'escalations': 0, 'mechanisms': {}, 'gate_passed': True, 'oracle_passed': True, 'oracle_reason': '', 'failed_calls': 0, 'stop_reason': None, 'note': None,
                'run_id': None, 'fail_stage': None}

    def test_a_suite_runs_every_task_in_level_order_and_saves_a_result(self):
        runner, temp = self.build(self.passing)
        runner.run()
        snap = runner.snapshot()
        self.assertEqual(snap['state'], 'done')
        self.assertEqual([r['task'] for r in snap['records']], ['hello', 'upper', 'sum'])
        saved = load_result(Path(temp.name), runner.id)
        self.assertEqual(saved['aggregate']['overall']['passed'], 3)
        self.assertEqual(list_results(Path(temp.name))[0]['id'], runner.id)

    def test_repeats_run_round_by_round_so_a_cut_off_suite_still_covers_every_task(self):
        runner, _ = self.build(self.passing, repeats=2, total_cloud_tokens=35_000)
        runner.run()
        records = runner.snapshot()['records']
        ran = [r for r in records if r['status'] != SKIPPED]
        self.assertEqual(len(ran), 4)  # 每次 1 万，到 3.5 万之前能跑 4 次 / 10k each, 4 runs fit under 35k
        self.assertEqual([r['task'] for r in ran[:3]], ['hello', 'upper', 'sum'])  # 先把每个任务各跑一遍 / one pass over every task first
        self.assertTrue(all('上限' in (r['note'] or '') for r in records if r['status'] == SKIPPED))

    def test_the_time_cap_skips_the_rest(self):
        runner, _ = self.build(self.passing, total_minutes=2)
        runner.run()
        statuses = [r['status'] for r in runner.snapshot()['records']]
        self.assertEqual(statuses, [PASS, PASS, SKIPPED])  # 每个任务耗 1 分钟，2 分钟后停 / 1 minute each, stop after 2

    def test_per_run_limits_shrink_to_what_is_left_of_the_suite(self):
        runner, _ = self.build(self.passing, total_cloud_tokens=25_000)
        runner.run()
        self.assertLessEqual(self.limits[2]['cloud_tokens'], 5_000)  # 总额只剩 5000：单次运行不能超过它 / only 5000 left of the suite
        self.assertEqual(self.limits[0]['cloud_tokens'], 25_000)  # 总额 2.5 万 < 任务默认 4 万：先被总额截断 / the suite total caps the task default of 40k
        roomy, _ = self.build(self.passing, total_cloud_tokens=1_000_000)
        self.limits.clear()
        roomy.run()
        self.assertEqual(self.limits[0]['cloud_tokens'], BY_ID['hello'].max_cloud_tokens)  # 总额充裕时用任务自己的上限 / plenty left: the task's own limit

    def test_stop_cancels_the_rest(self):
        holder = {}

        def stopping(task, repeat, limits):
            holder['runner'].stop()
            return self.passing(task, repeat, limits)

        runner, _ = self.build(stopping)
        holder['runner'] = runner
        runner.run()
        snap = runner.snapshot()
        self.assertEqual((snap['state'], [r['status'] for r in snap['records']]), ('stopped', [PASS, SKIPPED, SKIPPED]))

    def test_a_crashing_task_is_recorded_as_an_error_and_the_suite_continues(self):
        def flaky(task, repeat, limits):
            if task.id == 'upper':
                raise RuntimeError('boom')
            return self.passing(task, repeat, limits)

        runner, _ = self.build(flaky)
        runner.run()
        self.assertEqual([r['status'] for r in runner.snapshot()['records']], [PASS, 'error', PASS])

    def test_config_validation(self):
        for bad in ({'suite': 'nope'}, {'task_ids': ['ghost']}, {'repeats': 0}, {'total_cloud_tokens': 5}, {'per_run_scale': 1000}, {'repeats': '2'}):
            with self.subTest(bad=bad), self.assertRaises(MasaError):
                resolve_config(bad)
        self.assertEqual(resolve_config({'suite': 'quick'})['task_ids'], SUITES['quick']['tasks'])
        with self.assertRaises(MasaError):
            load_result(Path('.'), '../etc')


if __name__ == '__main__':
    unittest.main()
