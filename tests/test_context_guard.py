"""硬上下文拦截：本地服务静默截断，所以必须在发请求之前拒绝几乎一定溢出的输入。
Hard context guard: local servers silently truncate, so near-certain overflows are rejected BEFORE the request is sent."""
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.domain.models import ContextOverflow, MasaError, stage_error
from masa.domain.tokens import estimate_tokens, estimate_tokens_lower
from masa.infrastructure.llm import ChatProvider


class NetworkReached(Exception):
    pass


class Opener:
    calls = 0

    def open(self, *args, **kwargs):
        Opener.calls += 1
        raise NetworkReached()


def provider(model_type, limit, protocol=None):
    config = {'base_url': 'http://127.0.0.1:11434' if model_type == 'local' else 'https://api.example.com/v1', 'model': 'm',
              'model_type': model_type, 'context_limit': limit, 'max_output_tokens': 256,
              'protocol': protocol or ('ollama' if model_type == 'local' else 'openai')}
    return ChatProvider(config, '' if model_type == 'local' else 'synthetic-key')


def context(chars):
    return {'purpose': 'project_planner', 'goal': 'x ' * (chars // 2)}


class ContextGuardTests(unittest.TestCase):
    def setUp(self):
        Opener.calls = 0
        patcher = patch('masa.infrastructure.llm.urllib.request.build_opener', lambda *a: Opener())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_local_overflow_is_rejected_before_any_request(self):
        with self.assertRaises(ContextOverflow) as caught:
            provider('local', 1024).respond(context(40_000))
        self.assertEqual(Opener.calls, 0)
        self.assertGreater(caught.exception.estimated_tokens, 1024)
        self.assertEqual(caught.exception.limit, 1024)
        self.assertIn('silently truncate', str(caught.exception))

    def test_an_input_that_fits_reaches_the_server(self):
        with self.assertRaises(NetworkReached):
            provider('local', 8192).respond(context(4_000))
        self.assertEqual(Opener.calls, 1)

    def test_cloud_models_are_not_guarded_because_they_fail_loudly(self):
        with self.assertRaises(NetworkReached):
            provider('cloud', 1024).respond(context(40_000))
        self.assertEqual(Opener.calls, 1)

    def test_the_guard_uses_a_lower_bound_so_borderline_inputs_still_run(self):
        # 保守估算认为放不下，但下界并不确定溢出：不拦，让服务端去跑。 The pessimistic estimate says no; the lower bound is not sure, so it runs.
        text = context(20_000)
        limit = 5500
        self.assertGreater(estimate_tokens(text), limit)
        with self.assertRaises(NetworkReached):
            provider('local', limit).respond(text)
        self.assertEqual(Opener.calls, 1)

    def test_overflow_keeps_its_type_through_stage_wrappers(self):
        original = ContextOverflow(9000, 4096, 'm')
        kept = stage_error(original, 'repair failed; run x')
        self.assertIsInstance(kept, ContextOverflow)
        self.assertEqual((kept.estimated_tokens, kept.limit), (9000, 4096))
        self.assertNotIsInstance(stage_error(MasaError('other'), 'repair failed; run x'), ContextOverflow)

    def test_estimators_are_ordered_and_cjk_aware(self):
        sample = {'goal': '实现区间归并 CLI ' * 50, 'code': 'func main() {}\n' * 100}
        self.assertLess(estimate_tokens_lower(sample), estimate_tokens(sample))
        self.assertGreater(estimate_tokens('中' * 100), estimate_tokens('a' * 100))


class CalibrationTests(unittest.TestCase):
    def test_fit_recovers_known_coefficients(self):
        from masa.domain.tokens import fit
        rows = [(a, n, round(a * 0.25 + n * 0.5)) for a, n in ((1000, 0), (4000, 300), (200, 900), (8000, 50), (50, 2000))]
        ascii_rate, other_rate = fit(rows)
        self.assertAlmostEqual(ascii_rate, 0.25, places=2)
        self.assertAlmostEqual(other_rate, 0.5, places=2)

    def test_configure_loads_valid_files_and_ignores_bad_ones(self):
        import json
        import tempfile
        from masa.domain import tokens
        saved = (tokens.HIGH, tokens.LOW)
        self.addCleanup(lambda: setattr(tokens, 'HIGH', saved[0]) or setattr(tokens, 'LOW', saved[1]))
        with tempfile.TemporaryDirectory() as temp:
            good = Path(temp) / 'good.json'
            good.write_text(json.dumps({'high': [0.4, 0.7], 'low': [0.2, 0.4]}))
            self.assertTrue(tokens.configure(good))
            self.assertEqual(tokens.HIGH, (0.4, 0.7))
            self.assertGreater(tokens.estimate_tokens('a' * 1000), 399)
            for content in ('not json', json.dumps({'high': [9, 9], 'low': [1, 1]}), json.dumps({'high': [0.1, 0.1], 'low': [0.2, 0.2]}), json.dumps({})):
                bad = Path(temp) / 'bad.json'
                bad.write_text(content)
                self.assertFalse(tokens.configure(bad))
            self.assertFalse(tokens.configure(Path(temp) / 'missing.json'))
            self.assertEqual(tokens.HIGH, (0.4, 0.7))  # 坏文件不改变已加载的系数 / bad files never change loaded values


if __name__ == '__main__':
    unittest.main()
