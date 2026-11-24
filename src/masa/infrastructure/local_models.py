"""Serve models from a local operator's catalog. HTTP callers select IDs only."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.request import ProxyHandler, build_opener

from masa.domain.models import MasaError
from masa.infrastructure.engine_process import start_engine
from masa.infrastructure.llm import NoRedirect
from masa.infrastructure.ollama import OllamaControl
from masa.infrastructure.proc import kill_group


@dataclass(frozen=True)
class LocalModel:
    id: str
    engine: str
    name: str
    model: str
    context_limit: int
    port: int
    path: Path | None = None
    cpu_moe: int | None = None

    @property
    def protocol(self):
        return 'ollama' if self.engine == 'ollama' else 'openai'

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.port}' + ('' if self.protocol == 'ollama' else '/v1')

    def matches(self, profile):
        return (profile['model_type'] == 'local' and profile['protocol'] == self.protocol
                and profile['base_url'] == self.base_url and profile['model'] == self.model)


class LocalModels:
    def __init__(self, root):
        self.path = root / 'local-models.json'
        self.log = root / 'local-engine.log'
        self.engines = {}
        self.models = {}
        self.error = None
        self.active = None
        self.process = None
        self.close_job = None
        self.ollama = OllamaControl()
        if self.path.is_file():
            try:
                self.engines, self.models = self.read_catalog()
            except (OSError, ValueError, TypeError, KeyError, AttributeError, MasaError):
                self.error = '本地模型配置无效，请检查 ' + str(self.path)

    def read_catalog(self):
        data = json.loads(self.path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or set(data) != {'engines', 'models'}:
            raise ValueError('invalid catalog')
        if set(data['engines']) - {'strata', 'llama.cpp', 'ollama'}:
            raise ValueError('unknown engine')
        # Older catalogs included Strata launch settings. Strata now runs externally.
        engines = {engine: self.absolute(path) for engine, path in data['engines'].items() if engine != 'strata'}
        models = {}
        for entry in data['models']:
            ident, engine, name, model = (entry[k] for k in ('id', 'engine', 'name', 'model'))
            if engine == 'strata':
                continue
            if (not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', ident)
                    or ident in models or engine not in engines):
                raise ValueError('invalid model ID or engine')
            if any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in (name, model)):
                raise ValueError('invalid model name')
            context = entry.get('context_limit', 16384)
            port = entry.get('port', {'llama.cpp': 8802, 'ollama': 11434}[engine])
            cpu_moe = entry.get('cpu_moe')
            if type(context) is not int or not 512 <= context <= 262144:
                raise ValueError('invalid context')
            if (type(port) is not int or not 1024 <= port <= 65535
                    or (port == 11434) != (engine == 'ollama')):
                raise ValueError('invalid port')
            if cpu_moe is not None and (engine != 'llama.cpp' or type(cpu_moe) is not int or not 0 <= cpu_moe <= 512):
                raise ValueError('invalid CPU MoE count')
            path = self.absolute(entry['path']) if engine != 'ollama' else None
            models[ident] = LocalModel(ident, engine, name, model, context, port, path, cpu_moe)
        return engines, models

    @staticmethod
    def absolute(path):
        candidate = Path(os.path.expandvars(path)).expanduser()
        if not candidate.is_absolute():
            raise ValueError('an absolute local path is required')
        return candidate

    def loaded(self):
        return self.active if self.process is None or self.process.poll() is None else None

    def availability(self, profile):
        """While a managed model is resident it is the only local routing candidate."""
        active = self.loaded()
        if active:
            return active.matches(profile)
        return not any(m.engine != 'ollama' and m.matches(profile) for m in self.models.values())

    def pinned(self, profile):
        active = self.loaded()
        return active is not None and active.matches(profile)

    def catalog(self):
        active = self.loaded()
        return {'path': str(self.path), 'error': self.error, 'log': str(self.log),
                'active_id': active.id if active else None,
                'models': [{'id': m.id, 'name': m.name, 'model': m.model, 'engine': m.engine,
                            'base_url': m.base_url, 'path': str(m.path) if m.path else None,
                            'executable': str(self.engines[m.engine])} for m in self.models.values()]}

    def command(self, model):
        engine = self.engines[model.engine]
        if not engine.is_file() or (model.path is not None and not model.path.is_file()):
            raise MasaError('local engine executable or model file is missing')
        if model.engine == 'ollama':
            return [str(engine), 'serve'], engine.parent
        command = [str(engine), '-m', str(model.path), '--alias', model.model, '-c', str(model.context_limit),
                   '-np', '1', '-ngl', '99', '-fa', 'on', '-ctk', 'q8_0', '-ctv', 'q8_0', '-t', '12',
                   '--jinja', '--reasoning', 'off', '--host', '127.0.0.1', '--port', str(model.port)]
        if model.cpu_moe is not None:
            command.extend(['--n-cpu-moe', str(model.cpu_moe)])
        return command, engine.parent

    @staticmethod
    def reachable(port):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                return True
        except OSError:
            return False

    def ready(self, model):
        try:
            if model.engine == 'ollama':
                return bool(self.ollama.request('/api/version', timeout=1).get('version'))
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(f'http://127.0.0.1:{model.port}/health', timeout=1) as response:
                health = json.loads(response.read(65536))
            if health.get('status') != 'ok':
                return False
            with opener.open(model.base_url + '/models', timeout=1) as response:
                data = json.loads(response.read(65536))['data']
            return any(item.get('id') == model.model and item.get('status', {}).get('value') != 'unloaded'
                       for item in data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, MasaError):
            return False

    def start(self, ident, cancel):
        if not isinstance(ident, str) or ident not in self.models:
            raise MasaError(self.error or 'unknown local model ID')
        model = self.models[ident]
        command, cwd = self.command(model)
        current = self.loaded()
        # A previous MASA instance or a standalone engine may still own a catalog port.
        for port in {m.port for m in self.models.values() if m.engine != 'ollama'}:
            if (current is None or port != current.port) and self.reachable(port):
                raise MasaError(f'端口 {port} 仍有外部服务运行。请先释放该引擎，再启动所选模型。')
        if current == model and self.ready(model):
            self.release_other_ollama_models(model)
            return model
        self.stop()
        borrowed = self.reachable(model.port)
        if borrowed and (model.engine != 'ollama' or not self.ready(model)):
            raise MasaError(f'Port {model.port} is already in use. Stop the other service or change the catalog port.')
        self.release_other_ollama_models(model)
        try:
            if not borrowed:
                env = os.environ.copy()
                if model.engine == 'ollama':
                    env['OLLAMA_HOST'] = '127.0.0.1:11434'
                    env['OLLAMA_CONTEXT_LENGTH'] = str(model.context_limit)
                with self.log.open('wb') as log:
                    self.process, self.close_job = start_engine(command, cwd, env, log)
                deadline = time.monotonic() + 1080
                while not self.ready(model):
                    if self.process.poll() is not None:
                        raise MasaError(f'{model.engine} stopped during startup. See {self.log}')
                    if time.monotonic() >= deadline or cancel.wait(0.5):
                        raise MasaError(f'{model.engine} startup did not complete. See {self.log}')
            if cancel.is_set():
                raise MasaError('local model startup cancelled')
            if model.engine == 'ollama':
                self.ollama.residency(model.model, True, keep_alive=-1, context_limit=model.context_limit)
                self.active = model
                if not any(r.get('name') == model.model for r in self.ollama.request('/api/ps').get('models', [])):
                    raise MasaError('Ollama did not report the model as loaded')
            self.active = model
            return model
        except Exception:
            self.stop()
            raise

    def release_other_ollama_models(self, target):
        # This also covers models loaded via the legacy Ollama panel or outside MASA.
        if not self.reachable(11434):
            return
        keep = target.model if target.engine == 'ollama' else None

        def others():
            return {row['name'] for row in self.ollama.request('/api/ps', timeout=3)['models']
                    if row['name'] != keep}

        running = others()
        if not running:
            return
        for name in running:
            self.ollama.residency(name, False)
        deadline = time.monotonic() + 30
        while others():
            if time.monotonic() >= deadline:
                raise MasaError('Ollama 仍有其他模型驻留，已中止新模型启动。')
            time.sleep(0.25)

    def stop(self):
        process, model = self.process, self.active
        if model and model.engine == 'ollama' and process is None and self.reachable(model.port):
            self.ollama.residency(model.model, False)
            deadline = time.monotonic() + 10
            while any(row.get('name') == model.model for row in self.ollama.request('/api/ps', timeout=2).get('models', [])):
                if time.monotonic() >= deadline:
                    raise MasaError('Ollama is still unloading the model. Try again after it releases memory.')
                time.sleep(0.25)
        if process is not None:
            if self.close_job is not None:
                self.close_job()
                self.close_job = None
            else:
                kill_group(process.pid)
            process.wait(timeout=10)
            if model:
                deadline = time.monotonic() + 10
                while self.reachable(model.port):
                    if time.monotonic() >= deadline:
                        raise MasaError(f'旧引擎端口 {model.port} 仍在监听，切换已中止。')
                    time.sleep(0.25)
        self.process = self.active = None
