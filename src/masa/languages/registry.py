"""语言包管理器：发现、加载、校验、就绪探测、默认语言。 Language-pack manager: discovery, loading, validation, readiness, default language.

内置包在 masa/languages/<id>/（含 pack.json 与 pack.py）；用户包放在设置里配置的目录（或环境变量 MASA_PACKS_DIR）。
Built-in packs live in masa/languages/<id>/ (pack.json + pack.py); user packs live in the directory configured in Settings (or MASA_PACKS_DIR).
外部包是在本进程里执行的 Python 代码，等同插件：只从用户明确配置的目录加载，不自动下载。
An external pack is Python code run in this process, i.e. a plugin: only loaded from a directory the user configured, never downloaded.
"""
import hashlib
import importlib
import importlib.util
import os
import threading
from pathlib import Path

from masa.domain.models import MasaError
from masa.languages.base import API_VERSION, LanguagePack, load_manifest

BUILTIN_DIR = Path(__file__).resolve().parent
DEFAULT_LANGUAGE = 'go'


class Registry:
    def __init__(self):
        self._lock = threading.RLock()
        self._packs = {}
        self._errors = {}
        self._configs = {}
        self._default = DEFAULT_LANGUAGE
        self._extra_dirs = ()
        self._loaded = False

    # ---------- 加载 / loading ----------
    def _load_dir(self, folder, builtin):
        manifest_path = folder / 'pack.json'
        if not manifest_path.is_file():
            return
        name = folder.name
        try:
            manifest = load_manifest(manifest_path)
            if builtin:
                module = importlib.import_module(f'masa.languages.{name}.pack')
            else:
                spec = importlib.util.spec_from_file_location(f'masa_user_pack_{name}', folder / 'pack.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            pack = module.create(manifest, folder)
            if not isinstance(pack, LanguagePack):
                raise ValueError('create() must return a LanguagePack')
            if pack.id in self._packs:
                raise ValueError(f'duplicate language id {pack.id}')
            self._packs[pack.id] = pack
        except Exception as exc:  # 一个坏包不能影响其它包 / one broken pack must not affect the others
            self._errors[name] = f'{type(exc).__name__}: {exc}'[:300]

    def load(self, force=False):
        with self._lock:
            if self._loaded and not force:
                return
            self._packs, self._errors = {}, {}
            for folder in sorted(BUILTIN_DIR.iterdir()):
                if folder.is_dir() and not folder.name.startswith('_'):
                    self._load_dir(folder, True)
            dirs = list(self._extra_dirs) + ([Path(os.environ['MASA_PACKS_DIR'])] if os.environ.get('MASA_PACKS_DIR') else [])
            for directory in dirs:
                if directory.is_dir():
                    for folder in sorted(directory.iterdir()):
                        if folder.is_dir():
                            self._load_dir(folder, False)
            for ident, pack in self._packs.items():
                pack.config = dict(self._configs.get(ident, {}))
            self._loaded = True

    # ---------- 配置 / configuration ----------
    def configure(self, default=None, configs=None, extra_dirs=None):
        """由 Settings 调用：默认语言、每个包的配置、额外的包目录。 Called by Settings: the default language, per-pack configs and extra pack directories."""
        with self._lock:
            reload = False
            if default:
                self._default = default
            if configs is not None:
                self._configs = {k: dict(v) for k, v in configs.items()}
                for ident, pack in self._packs.items():
                    pack.config = dict(self._configs.get(ident, {}))
            if extra_dirs is not None and tuple(Path(p) for p in extra_dirs) != self._extra_dirs:
                self._extra_dirs = tuple(Path(p) for p in extra_dirs)
                reload = True
            if reload:
                self.load(force=True)

    # ---------- 查询 / queries ----------
    def get(self, ident=None):
        """按 id 取包；None 取默认语言。找不到抛 MasaError。 A pack by id; None means the default language."""
        self.load()
        ident = ident or self._default
        pack = self._packs.get(ident)
        if pack is None:
            raise MasaError(f'language pack {ident!r} is not available' + (f' ({self._errors[ident]})' if ident in self._errors else ''))
        return pack

    def for_run(self, data):
        """一个运行/任务数据对应的语言包；缺省视为默认语言（旧数据都是 go）。 The pack of a run's data; missing means the default (old data is all go)."""
        return self.get((data or {}).get('language') or DEFAULT_LANGUAGE)

    def default_id(self):
        return self._default

    def ids(self):
        self.load()
        return sorted(self._packs)

    def errors(self):
        self.load()
        return dict(self._errors)

    def all_operation_ids(self):
        self.load()
        return frozenset(i for p in self._packs.values() for i in p.operation_ids())

    def pack_of_operation(self, operation_id):
        self.load()
        for pack in self._packs.values():
            if operation_id in pack.operation_ids():
                return pack
        raise MasaError(f'unknown operation {operation_id!r}')

    def digest(self, ident=None):
        """包清单与包代码的哈希，写进账本画像。 Hash of the manifest and code, recorded in the ledger profile."""
        pack = self.get(ident)
        h = hashlib.sha256()
        root = pack.root
        if root:
            for path in sorted(Path(root).rglob('*')):
                if path.is_file() and path.suffix in {'.json', '.py', '.md'} and '__pycache__' not in path.parts:
                    h.update(path.relative_to(root).as_posix().encode())
                    h.update(path.read_bytes())
        return h.hexdigest()

    def describe(self, executor=None):
        """给设置页与 bootstrap：每个包的说明、就绪状态、配置项。 For Settings and bootstrap: description, readiness and config of every pack."""
        self.load()
        out = []
        for ident in sorted(self._packs):
            pack = self._packs[ident]
            info = pack.describe()
            try:
                info['readiness'] = pack.probe(executor).to_dict()
            except Exception as exc:
                info['readiness'] = {'ok': False, 'details': [f'probe failed: {exc}'[:200]]}
            info['config_values'] = dict(pack.config)
            info['default'] = ident == self._default
            info['digest'] = self.digest(ident)[:12]
            out.append(info)
        for name, error in sorted(self._errors.items()):
            out.append({'id': name, 'title': name, 'error': error, 'readiness': {'ok': False, 'details': [error]}})
        return out


registry = Registry()
__all__ = ['registry', 'Registry', 'API_VERSION', 'DEFAULT_LANGUAGE']
