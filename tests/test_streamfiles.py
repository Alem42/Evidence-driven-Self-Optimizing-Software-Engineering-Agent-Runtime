"""从没写完的 JSON 里取文件：每个前缀都要安全，且内容只增不改。
Reading files out of unfinished JSON: every prefix must be safe and the content may only grow, never change."""
import json
import unittest

from masa.runtime.streamfiles import extract_files

CODE = 'package wc\n\n// 统计 "字数"\nfunc Count(s string) int {\n\treturn len(s) // \\n 不是换行\n}\n😀\n'


def bundle(**files):
    return json.dumps({'files': files}, ensure_ascii=False)


class PrefixTests(unittest.TestCase):
    def check_prefixes(self, text, expected):
        """对 text 的每一个前缀：不抛异常；每个文件的内容是最终内容的前缀；complete 只在内容完整时为真。
        For every prefix: no exception; each file's content is a prefix of its final content; complete only when it is whole."""
        final = {f['path']: f['content'] for f in extract_files(text)}
        self.assertEqual(final, expected)
        seen = {}
        for n in range(len(text) + 1):
            for f in extract_files(text[:n]):
                path, content = f['path'], f['content']
                self.assertTrue(expected[path].startswith(content), (n, path, content[-30:]))
                self.assertGreaterEqual(len(content), len(seen.get(path, '')), (n, path))  # 只增不减 / only grows
                seen[path] = content
                if f['complete']:
                    self.assertEqual(content, expected[path], (n, path))

    def test_every_prefix_of_a_multi_file_bundle_is_safe_and_monotone(self):
        files = {'cmd/app/main.go': 'package main\nfunc main() {}\n', 'internal/wc/wc.go': CODE, 'internal/wc/wc_test.go': 'package wc\n// "引号" \\ 反斜杠\n'}
        self.check_prefixes(bundle(**files), files)

    def test_an_escape_written_halfway_is_dropped_not_garbled(self):
        text = '{"files":{"a.go":"x\\u00'
        self.assertEqual(extract_files(text)[0]['content'], 'x')  # \u00 还没写完 / the \u00 is incomplete
        self.assertFalse(extract_files(text)[0]['complete'])
        self.assertEqual(extract_files('{"files":{"a.go":"x\\')[0]['content'], 'x')  # 结尾是孤零零的反斜杠 / a lone trailing backslash
        self.assertEqual(extract_files('{"files":{"a.go":"x\\u0041y"}}')[0]['content'], 'xAy')

    def test_surrogate_pairs_become_one_character(self):
        self.assertEqual(extract_files('{"files":{"a.go":"\\ud83d\\ude00"}}')[0]['content'], '😀')

    def test_the_file_being_written_is_incomplete_and_the_earlier_ones_are_complete(self):
        text = '{"files":{"a.go":"package a\\n","b.go":"package b\\nfunc F('
        found = {f['path']: f for f in extract_files(text)}
        self.assertTrue(found['a.go']['complete'])
        self.assertFalse(found['b.go']['complete'])
        self.assertEqual(found['b.go']['content'], 'package b\nfunc F(')

    def test_a_key_that_is_still_being_written_gives_nothing_yet(self):
        self.assertEqual(extract_files('{"files":{"internal/wc'), [])
        self.assertEqual(extract_files('{"files":'), [])
        self.assertEqual(extract_files(''), [])
        self.assertEqual(extract_files('no json at all'), [])

    def test_markdown_fences_and_leading_prose_are_skipped(self):
        self.assertEqual(extract_files('```json\n{"files":{"a.go":"x"}}\n```')[0]['content'], 'x')

    def test_v2_worker_write_and_edit_actions(self):
        write = json.dumps({'tool': 'write_file', 'path': 'internal/wc/wc.go', 'content': CODE}, ensure_ascii=False)
        self.check_prefixes_for_action(write, 'write', CODE)
        edit = json.dumps({'tool': 'edit_file', 'path': 'a.go', 'old': 'return 1', 'new': 'return 2'})
        found = extract_files(edit)[0]
        self.assertEqual((found['kind'], found['content'], found['complete']), ('edit', 'return 2', True))

    def check_prefixes_for_action(self, text, kind, content):
        shown = ''
        for n in range(len(text) + 1):
            for f in extract_files(text[:n]):
                self.assertEqual(f['kind'], kind)
                self.assertTrue(content.startswith(f['content']))
                self.assertGreaterEqual(len(f['content']), len(shown))
                shown = f['content']
        self.assertEqual(shown, content)

    def test_actions_without_a_known_path_or_with_other_tools_are_ignored(self):
        self.assertEqual(extract_files('{"tool":"run_check","kind":"test"}'), [])
        self.assertEqual(extract_files('{"tool":"write_file","content":"x"}'), [])  # 内容在路径之前：路径未知就不显示 / content before the path: no path yet, show nothing
        self.assertEqual(extract_files('{"tool":"write_file","path":"a.go'), [])

    def test_arrays_and_other_values_before_files_do_not_confuse_the_reader(self):
        text = '{"notes":["a ] b","\\"x\\""],"n":12,"ok":true,"files":{"a.go":"x"}}'
        self.assertEqual(extract_files(text)[0]['content'], 'x')

    def test_garbage_never_raises(self):
        for text in ('{', '{"', '{"files":{"a":', '{"files":{"a":"x"', '{"files":{"a":12}}', '{"files":[1,2]}', '[{"files":{}}]', '{"files":{"a.go":"\\'):
            extract_files(text)  # 不抛异常即可 / just must not raise


if __name__ == '__main__':
    unittest.main()
