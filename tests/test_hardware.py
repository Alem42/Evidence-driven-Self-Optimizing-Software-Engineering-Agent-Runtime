"""硬件监控的边界与降级测试。 Hardware-monitor bounds and degradation tests."""

import io
import json
import time
import unittest
from unittest.mock import patch

from masa.infrastructure import hardware


class FakeProcess:
    def __init__(self, output=b'', returncode=0, read_delay=0):
        self.stdout = io.BytesIO(output)
        self.returncode = returncode
        self.killed = False
        if read_delay:
            original_read = self.stdout.read

            def read(size):
                time.sleep(read_delay)
                return original_read(size)

            self.stdout.read = read

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.stdout.close()

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        return self.returncode

    def poll(self):
        return self.returncode


class HardwareTests(unittest.TestCase):
    def test_gpu_units_unknown_and_multiple_devices(self):
        result = hardware._gpu_result('0, RTX Test, 16384, 2048, 14336, 17\n1, Second GPU, N/A, 0, N/A, [N/A]\n')
        self.assertTrue(result['available'])
        self.assertEqual(result['devices'][0]['total_mib'], 16384)
        self.assertEqual(result['devices'][0]['utilization_percent'], 17)
        self.assertIsNone(result['devices'][1]['total_mib'])
        self.assertEqual(result['devices'][1]['used_mib'], 0)
        self.assertIsNone(result['devices'][1]['utilization_percent'])
        with self.assertRaises(hardware._ProbeError):
            hardware._gpu_result('0, Missing columns\n')

    def test_memory_units_and_missing_speed(self):
        result = hardware._memory_result(json.dumps({'total_bytes': 32 * 1024**3, 'modules': [
            {'Capacity': 16 * 1024**3, 'Speed': 6000, 'ConfiguredClockSpeed': 5600},
            {'Capacity': 16 * 1024**3, 'Speed': 0},
        ]}))
        self.assertEqual(result['total_bytes'], 32 * 1024**3)
        self.assertEqual(result['modules'][0]['configured_speed_mhz'], 5600)
        self.assertIsNone(result['modules'][1]['speed_mhz'])
        self.assertIsNone(result['modules'][1]['configured_speed_mhz'])
        for raw in ('[]', '{"modules":{}}', '{"modules":[null]}'):
            with self.assertRaises(hardware._ProbeError):
                hardware._memory_result(raw)

    def test_capture_output_limit_and_failure_hide_error(self):
        process = FakeProcess(b'a' * (hardware._OUTPUT_LIMIT + 1))
        with patch.object(hardware.subprocess, 'Popen', return_value=process) as spawn:
            with self.assertRaisesRegex(hardware._ProbeError, 'output_limit'):
                hardware._capture(['fixed-tool'], 1)
        self.assertTrue(process.killed)
        self.assertFalse(spawn.call_args.kwargs['shell'])
        self.assertEqual(spawn.call_args.kwargs['stderr'], hardware.subprocess.DEVNULL)
        with patch.object(hardware.subprocess, 'Popen', return_value=FakeProcess(b'private error', 1)):
            with self.assertRaisesRegex(hardware._ProbeError, '^query_failed$'):
                hardware._capture(['fixed-tool'], 1)

    def test_capture_timeout_kills_without_retry(self):
        process = FakeProcess(read_delay=0.05)
        with patch.object(hardware.subprocess, 'Popen', return_value=process) as spawn:
            with self.assertRaisesRegex(hardware._ProbeError, '^timeout$'):
                hardware._capture(['fixed-tool'], 0.005)
        self.assertTrue(process.killed)
        self.assertEqual(spawn.call_count, 1)

    def test_queries_are_fixed_and_do_not_elevate(self):
        with patch.object(hardware.shutil, 'which', return_value='nvidia-smi'), \
             patch.object(hardware, '_capture', return_value='0, RTX, 100, 20, 80, 4') as capture:
            self.assertTrue(hardware.HardwareMonitor()._gpu()['available'])
        self.assertEqual(capture.call_args.args[0], [
            'nvidia-smi', '--query-gpu=' + hardware._GPU_FIELDS, '--format=csv,noheader,nounits',
        ])
        with patch.object(hardware.os, 'name', 'nt'), \
             patch.object(hardware.shutil, 'which', return_value='powershell'), \
             patch.object(hardware, '_capture', return_value='{"total_bytes":1024,"modules":[]}') as capture:
            self.assertTrue(hardware.HardwareMonitor()._memory()['available'])
        command = capture.call_args.args[0]
        self.assertEqual(command[:-1], ['powershell', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command'])
        self.assertEqual(command[-1], hardware._MEMORY_QUERY)
        self.assertNotIn('RunAs', command[-1])

    def test_failures_and_platform_are_unknown(self):
        with patch.object(hardware.shutil, 'which', return_value=None):
            self.assertEqual(hardware.HardwareMonitor()._gpu()['warning'], 'tool_not_found')
        with patch.object(hardware.os, 'name', 'posix'):
            memory = hardware.HardwareMonitor()._memory()
        self.assertFalse(memory['available'])
        self.assertIsNone(memory['total_bytes'])
        with patch.object(hardware.shutil, 'which', return_value='tool'), \
             patch.object(hardware, '_capture', side_effect=hardware._ProbeError('timeout')):
            self.assertEqual(hardware.HardwareMonitor()._gpu()['warning'], 'timeout')

    def test_cache_isolation_and_refresh(self):
        monitor = hardware.HardwareMonitor()
        gpu = {'available': True, 'devices': [{'name': 'GPU'}], 'warning': None}
        with patch.object(monitor, '_gpu', return_value=gpu) as probe, \
             patch.object(monitor, '_memory', return_value={'available': False}), \
             patch.object(hardware.time, 'monotonic', side_effect=[100, 101, 111, 112]):
            first = monitor.snapshot()
            first['gpu']['devices'][0]['name'] = 'modified'
            second = monitor.snapshot()
            self.assertEqual(second['gpu']['devices'][0]['name'], 'GPU')
            self.assertEqual(probe.call_count, 1)
            monitor.snapshot()
            self.assertEqual(probe.call_count, 2)


if __name__ == '__main__':
    unittest.main()
