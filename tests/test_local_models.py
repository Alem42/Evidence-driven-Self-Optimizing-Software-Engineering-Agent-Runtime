"""Regression checks for local model ownership and use in Project02."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

from masa.application.console import Console
from masa.domain.models import MasaError
from masa.infrastructure.engine_process import start_engine
from masa.infrastructure.local_models import LocalModel


class LocalModelsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        exe = root / 'ollama.exe'
        exe.touch()
        (root / 'local-models.json').write_text(json.dumps({
            'engines': {'ollama': str(exe)},
            'models': [
                {'id': 'first', 'engine': 'ollama', 'name': 'First', 'model': 'first:latest'},
                {'id': 'second', 'engine': 'ollama', 'name': 'Second', 'model': 'second:latest'},
            ],
        }), encoding='utf-8')
        self.console = Console(root, 'missing', 'missing', root)
        self.manager = self.console.local_models
        self.running = []
        control = Mock()
        control.request.side_effect = lambda path, **_: {'version': 'test'} if path == '/api/version' else {
            'models': [{'name': name} for name in self.running]}

        def residency(name, load, **_):
            if load:
                self.running.append(name)
            else:
                self.running.remove(name)

        control.residency.side_effect = residency
        self.manager.ollama = control
        self.manager.reachable = lambda _: True
        self.addCleanup(self.console.close)

    def action(self, action, model_id):
        job_id = self.console.local_model_action({'action': action, 'id': model_id})['job_id']
        self.console.job_thread.join(timeout=5)
        self.assertFalse(self.console.job_thread.is_alive())
        return self.console.project_job(job_id)

    def test_switch_releases_old_model_before_loading_and_pins_the_selected_profile(self):
        first = self.action('start', 'first')['result']['profile_id']
        self.assertTrue(self.console.settings.provider(first).keep_loaded)
        second = self.action('start', 'second')['result']['profile_id']
        self.assertEqual(self.running, ['second:latest'])
        self.assertEqual(self.console.settings.active_id, second)
        self.assertEqual([ident for ident, _ in self.console.settings.ready_profiles()], [second])
        self.assertEqual(self.manager.catalog()['active_id'], 'second')
        self.assertEqual(self.action('stop', 'first')['status'], 'failed')
        self.assertEqual(self.running, ['second:latest'])
        self.assertEqual(self.action('stop', 'second')['status'], 'completed')
        self.assertEqual(self.running, [])

    def test_switch_releases_untracked_ollama_tag_and_reuses_daemon(self):
        self.running.append('elsewhere')
        with patch('masa.infrastructure.local_models.start_engine') as popen:
            job = self.action('start', 'first')
        popen.assert_not_called()
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(self.running, ['first:latest'])
        self.assertEqual(self.manager.ollama.residency.call_args_list, [
            call('elsewhere', False), call('first:latest', True, keep_alive=-1, context_limit=16384)])

    def test_llama_waits_for_untracked_ollama_release_before_spawning(self):
        model = LocalModel('native', 'llama.cpp', 'Llama', 'llama', 16384, 8802)
        self.manager.models[model.id] = model
        self.manager.reachable = lambda port: port == 11434
        self.running.append('elsewhere')
        child = Mock()
        child.poll.return_value = 0

        def spawn(*args, **kwargs):
            self.assertEqual(self.running, [])
            return child, Mock()

        with patch.object(self.manager, 'command', return_value=(['llama-server'], None)), \
                patch.object(self.manager, 'ready', return_value=True), \
                patch('masa.infrastructure.local_models.start_engine', side_effect=spawn) as popen:
            self.manager.start(model.id, threading.Event())
        popen.assert_called_once()
        self.assertEqual(self.manager.active, model)

    def test_switch_aborts_if_ollama_refuses_release(self):
        self.running.append('elsewhere')
        self.manager.ollama.residency.side_effect = MasaError('unload failed')
        with patch('masa.infrastructure.local_models.start_engine') as popen:
            job = self.action('start', 'first')
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(self.running, ['elsewhere'])
        self.assertIsNone(self.manager.active)
        popen.assert_not_called()

    def test_stale_native_listener_blocks_starting_another_model(self):
        self.manager.models['native'] = LocalModel('native', 'llama.cpp', 'Llama', 'llama', 16384, 8802)
        job = self.action('start', 'first')
        self.assertEqual(job['status'], 'failed')
        self.assertIn('8802', job['error'])
        self.manager.ollama.residency.assert_not_called()

    def test_legacy_strata_catalog_entries_do_not_manage_or_gate_external_profiles(self):
        data = json.loads(self.manager.path.read_text(encoding='utf-8'))
        data['engines']['strata'] = 'old-install'
        data['models'].append({'id': 'strata', 'engine': 'strata', 'name': 'Strata', 'model': 'iq3_s'})
        self.manager.path.write_text(json.dumps(data), encoding='utf-8')
        engines, models = self.manager.read_catalog()
        self.assertNotIn('strata', engines)
        self.assertNotIn('strata', models)
        self.manager.models = models
        self.assertTrue(self.manager.availability({'model_type': 'local', 'protocol': 'openai',
                                                    'base_url': 'http://127.0.0.1:47831/v1', 'model': 'iq3_s'}))

    def test_rejects_model_actions_during_a_task(self):
        self.console.active = 'busy'
        try:
            with self.assertRaises(MasaError):
                self.console.local_model_action({'action': 'start', 'id': 'first'})
        finally:
            self.console.active = None

    def test_spawn_uses_only_catalog_arguments_and_cleans_up_a_failed_child(self):
        self.manager.reachable = lambda _: False
        self.manager.ready = lambda _: False
        child = Mock()
        child.poll.return_value = 1
        with patch('masa.infrastructure.local_models.start_engine', return_value=(child, Mock())) as popen:
            with self.assertRaisesRegex(MasaError, 'stopped during startup'):
                self.manager.start('first', threading.Event())
        args = popen.call_args.args[0]
        self.assertEqual(args, [str(self.manager.engines['ollama']), 'serve'])
        self.assertEqual(popen.call_args.args[2]['OLLAMA_HOST'], '127.0.0.1:11434')
        child.wait.assert_called_once()
        self.assertIsNone(self.manager.process)

    @unittest.skipUnless(os.name == 'nt', 'Windows process ownership')
    def test_windows_job_closes_the_engine_listener(self):
        root = self.manager.path.parent
        port_file = root / 'port'
        engine = """
import pathlib, socket, sys, time
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    pathlib.Path(sys.argv[1]).write_text(str(listener.getsockname()[1]))
    time.sleep(20)
"""
        with self.manager.log.open('wb') as log:
            launcher, close_job = start_engine([sys.executable, '-c', engine, str(port_file)],
                                               root, os.environ.copy(), log)
            try:
                deadline = time.monotonic() + 8
                while not port_file.is_file() or not port_file.stat().st_size:
                    self.assertLess(time.monotonic(), deadline, 'engine did not open a listener')
                    time.sleep(0.02)
                address = ('127.0.0.1', int(port_file.read_text()))
                with socket.create_connection(address, timeout=1):
                    pass
            finally:
                close_job()
                launcher.wait(timeout=5)
            with self.assertRaises(OSError):
                socket.create_connection(address, timeout=0.5)
