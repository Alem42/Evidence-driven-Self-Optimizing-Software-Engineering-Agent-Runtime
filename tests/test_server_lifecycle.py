"""服务生命周期独占与失败清理。 Exclusive service lifecycle and failed-start cleanup."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from masa.domain.models import MasaError
from masa.interfaces.http import server


class ServerLifecycleTests(unittest.TestCase):
    def test_second_console_cannot_initialize_jobs_on_another_port(self):
        """同目录第二服务不能构造 Console；避免误恢复仍在执行的任务。 Reject another Console before it can recover live Jobs."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            console = Mock()
            listener = Mock(server_port=8765)

            def serving(**kwargs):
                with self.assertRaisesRegex(MasaError, 'owns this state directory'):
                    server.serve(root, 'runner', 'go', root, port=8766)
                raise KeyboardInterrupt

            listener.serve_forever.side_effect = serving
            with patch.object(server, 'Console', return_value=console) as construct, \
                 patch.object(server, 'make_server', return_value=listener) as bind, \
                 redirect_stdout(io.StringIO()):
                server.serve(root, 'runner', 'go', root)
                construct.assert_called_once()
                bind.assert_called_once_with(console, 8765, server.DEFAULT_ORIGINS, bind='127.0.0.1', hosts=(), static_dir=None, demo=False)
            listener.server_close.assert_called_once()
            console.close.assert_called_once()

    def test_bind_failure_closes_console_and_releases_lifetime_lock(self):
        """绑定失败关闭已构造的 Console；后续启动不被陈旧锁阻止。 Close after bind failure and allow the next start."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = Mock(), Mock()
            listener = Mock(server_port=8766)
            listener.serve_forever.side_effect = KeyboardInterrupt
            with patch.object(server, 'Console', side_effect=[first, second]) as construct, \
                 patch.object(server, 'make_server', side_effect=[OSError('port in use'), listener]), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(OSError, 'port in use'):
                    server.serve(root, 'runner', 'go', root)
                first.close.assert_called_once()
                server.serve(root, 'runner', 'go', root, port=8766)
                self.assertEqual(construct.call_count, 2)
            second.close.assert_called_once()
            listener.server_close.assert_called_once()

    def test_listener_close_failure_still_closes_console(self):
        """监听器清理异常不能跳过 Console 清理或留下目录锁。 Always close Console and release ownership after listener cleanup errors."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            console = Mock()
            listener = Mock(server_port=8765)
            listener.serve_forever.side_effect = KeyboardInterrupt
            listener.server_close.side_effect = OSError('close failed')
            with patch.object(server, 'Console', return_value=console), \
                 patch.object(server, 'make_server', return_value=listener), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(OSError, 'close failed'):
                    server.serve(root, 'runner', 'go', root)
            console.close.assert_called_once()
            with server.owner_lock(root / 'console.lock'):
                pass
