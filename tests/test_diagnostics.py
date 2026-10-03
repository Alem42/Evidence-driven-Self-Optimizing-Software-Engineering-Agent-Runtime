"""故障日志的隐私与边界验证。 Privacy and bounded fault-log regressions."""
import tempfile
import unittest
from pathlib import Path
from masa.infrastructure.diagnostics import DiagnosticLog


class DiagnosticTests(unittest.TestCase):
    def test_rotation_and_corrupt_line_do_not_break_reading(self):
        """日志轮转并容忍部分写入，查询仍有界。 Rotate logs and tolerate partial writes while bounding reads."""
        with tempfile.TemporaryDirectory() as temp:
            log=DiagnosticLog(Path(temp),limit_bytes=500)
            for _ in range(8): log.record('worker:project',ValueError('secret'))
            self.assertTrue(log.path.with_suffix('.jsonl.1').exists())
            with log.path.open('a',encoding='utf-8') as handle: handle.write('partial-json\n')
            result=log.recent()
            self.assertTrue(result['errors'])
            self.assertLessEqual(len(result['errors']),20)
            self.assertNotIn('secret',log.path.read_text())
