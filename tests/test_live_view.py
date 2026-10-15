"""实时文件流：live 通道保留完整文本，Console.live_view 把它解成“正在写的文件”。 The live channel keeps the full text and Console.live_view decodes the file being written."""
import json
import tempfile
import unittest
from pathlib import Path

from masa.application.console import Console
from masa.runtime import live


class LiveViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.console = Console(Path(self.temp.name), Path('fake-runner'), Path('fake-go'), Path.cwd())
        self.addCleanup(self.console.close)
        self.rid = 'run-live-test'
        self.addCleanup(live.clear, self.rid)

    def test_nothing_in_flight_is_none(self):
        self.assertEqual(self.console.live_view('no-such-run'), {'live': None})

    def test_the_file_appears_piece_by_piece_and_a_new_call_resets_it(self):
        live.begin(self.rid, 'project_developer', 'file:2', 'deepseek-flash')
        answer = json.dumps({'files': {'internal/wc/wc.go': 'package wc\nfunc Count() int { return 1 }\n'}})
        seen = []
        for i in range(0, len(answer), 9):
            live.append(self.rid, answer[i:i + 9])
            view = self.console.live_view(self.rid)['live']
            seen.append(view['files'][0]['content'] if view['files'] else '')
        self.assertEqual(seen[-1], 'package wc\nfunc Count() int { return 1 }\n')
        self.assertTrue(all(b.startswith(a) for a, b in zip(seen, seen[1:])))  # 内容只增不改 / only grows
        final = self.console.live_view(self.rid)['live']
        self.assertTrue(final['files'][0]['complete'])
        self.assertEqual((final['step'], final['invocation'], final['model']), ('project_developer', 'file:2', 'deepseek-flash'))
        self.assertGreater(final['seq'], 3)
        live.begin(self.rid, 'project_repair', 'initial', 'deepseek-flash')  # 新调用：上一次的文本不能串进来 / a new call must not inherit the old text
        again = self.console.live_view(self.rid)['live']
        self.assertEqual((again['files'], again['step'], again['seq']), ([], 'project_repair', 0))

    def test_the_text_is_capped_and_flagged_instead_of_growing_without_bound(self):
        live.begin(self.rid, 'project_developer', 'initial', 'm')
        live.append(self.rid, '{"files":{"a.go":"' + 'x' * (live.MAX_TEXT - 40))
        live.append(self.rid, 'y' * 100)  # 越界：停止追加并标记 / over the cap: stop appending and flag it
        view = self.console.live_view(self.rid)['live']
        self.assertTrue(view['truncated'])
        self.assertLessEqual(len(live.get(self.rid)['text']), live.MAX_TEXT)
        self.assertNotIn('y', view['files'][0]['content'][-5:])


if __name__ == '__main__':
    unittest.main()
