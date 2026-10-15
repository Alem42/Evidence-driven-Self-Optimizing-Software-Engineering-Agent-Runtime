"""Python 项目环境（.tools/pyenv）：解释器默认值、装包规则、大小上限、策略（询问/完全访问/禁止）、triage 与提示词的联动。零网络、零 token。
The Python project environment (.tools/pyenv): the interpreter default, install rules, the size cap, the policy (ask / full access / deny) and how triage and prompts follow it. No network, no tokens.
"""
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.checks.triage import INFEASIBLE, OK, assess
from masa.domain.models import MasaError
from masa.languages.python import env
from masa.languages.python.pack import PythonPack as PythonPackModule
from masa.languages.registry import registry


def fake_python(root):
    """一个不会真的运行的“环境”：只有解释器文件存在。 A fake environment: only the interpreter file exists."""
    path = Path(root) / 'pyenv' / ('Scripts' if sys.platform == 'win32' else 'bin') / ('python.exe' if sys.platform == 'win32' else 'python')
    path.parent.mkdir(parents=True)
    path.write_text('', encoding='utf-8')
    return path


class EnvTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {'MASA_TOOLS_DIR': self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        env._TOP.clear()
        self.addCleanup(env._TOP.clear)

    def pack(self, **config):
        pack = registry.get('python')
        old = dict(pack.config)
        pack.config = {**old, **config}
        self.addCleanup(lambda: setattr(pack, 'config', old))
        return pack


class RequirementTests(EnvTestCase):
    def test_only_plain_pypi_names_with_an_optional_exact_version_are_accepted(self):
        self.assertEqual(env.clean_requirements(['numpy', 'Pandas==2.2.0', 'numpy']), ['numpy', 'Pandas==2.2.0'])
        for bad in ('git+https://evil/x.git', 'http://x/y.whl', './local', '../up', '-r requirements.txt', '--index-url=http://x', 'numpy>=1', 'numpy[extra]', 'a b', 'x;python_version<"3"', ''):
            with self.subTest(bad=bad), self.assertRaises(MasaError):
                env.clean_requirements([bad])
        with self.assertRaises(MasaError):
            env.clean_requirements([])
        with self.assertRaises(MasaError):
            env.clean_requirements(['p' + str(n) for n in range(21)])


class InstallTests(EnvTestCase):
    def fake_runner(self, listing, grows_to_mb=None):
        """记录每条命令；pip list 返回当前 listing；pip install 把新包加进 listing。 Records every command; `pip list` returns the listing, `pip install` adds the packages to it."""
        calls = []

        def run(args, timeout):
            calls.append([str(a) for a in args])
            joined = ' '.join(str(a) for a in args)
            if 'pip list' in joined or ('pip' in joined and 'list' in joined):
                import json
                return types.SimpleNamespace(returncode=0, stdout=json.dumps(listing), stderr='')
            if 'pip install' in joined:
                for name in args[args.index('--only-binary=:all:') + 1:]:
                    listing.append({'name': str(name).split('==')[0], 'version': '1.0'})
                return types.SimpleNamespace(returncode=0, stdout='ok', stderr='')
            if 'uninstall' in joined:
                gone = {str(a).lower() for a in args[args.index('-y') + 1:]}
                listing[:] = [p for p in listing if p['name'].lower() not in gone and p['name'] != '--disable-pip-version-check']
                return types.SimpleNamespace(returncode=0, stdout='ok', stderr='')
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        return run, calls

    def test_wheels_only_and_the_new_packages_are_reported(self):
        fake_python(self.temp.name)
        listing = [{'name': 'pip', 'version': '24'}]
        run, calls = self.fake_runner(listing)
        with patch.object(env, 'packages', side_effect=lambda: [p for p in listing if p['name'] != 'pip']):
            done = env.install(['numpy'], 500, run=run)
        self.assertEqual(done['installed'], ['numpy==1.0'])
        install = next(c for c in calls if 'install' in c)
        self.assertIn('--only-binary=:all:', install)  # 源码包会执行 setup.py：不允许 / an sdist would run setup.py: not allowed
        self.assertNotIn('--index-url', ' '.join(install))

    def test_going_over_the_size_cap_removes_what_was_just_installed_and_says_so(self):
        fake_python(self.temp.name)
        listing = [{'name': 'old', 'version': '1.0'}]
        run, calls = self.fake_runner(listing)
        with patch.object(env, 'packages', side_effect=lambda: list(listing)), patch.object(env, 'size_mb', return_value=612.0):
            with self.assertRaisesRegex(MasaError, r'612.0 MB, over the 500 MB cap'):
                env.install(['bigpkg'], 500, run=run)
        self.assertTrue(any('uninstall' in c and 'bigpkg' in c for c in calls))
        self.assertNotIn('old', [c for c in calls if 'uninstall' in c][0])  # 只撤销这次新装的 / only what this call installed
        self.assertEqual([p['name'] for p in listing], ['old'])

    def test_a_failed_pip_is_reported_with_its_message(self):
        fake_python(self.temp.name)
        run = lambda args, timeout: types.SimpleNamespace(returncode=1, stdout='', stderr='ERROR: No matching distribution found for nope')
        with patch.object(env, 'packages', return_value=[]), self.assertRaisesRegex(MasaError, 'No matching distribution'):
            env.install(['nope'], 500, run=run)


class PolicyTests(EnvTestCase):
    def test_ask_needs_approval_allow_installs_and_deny_refuses(self):
        pack = self.pack(package_policy='ask')
        environment = pack.environment()
        with patch.object(env, 'install', return_value={'installed': ['numpy==1.0'], 'size_mb': 12.0}) as installer, patch.object(env, 'exists', return_value=False):
            asked = environment.install(['numpy'])
            self.assertEqual((asked['needs_approval'], asked['packages']), (True, ['numpy']))
            installer.assert_not_called()  # 没批准就什么都没装 / nothing was installed without approval
            self.assertFalse(environment.install(['numpy'], approve=True)['needs_approval'])
            self.assertEqual(installer.call_count, 1)
            pack.config['package_policy'] = 'allow'
            self.assertFalse(environment.install(['numpy'])['needs_approval'])  # 完全访问：不再询问 / full access: no more asking
            pack.config['package_policy'] = 'deny'
            with self.assertRaisesRegex(MasaError, 'switched off'):
                environment.install(['numpy'], approve=True)

    def test_an_unknown_policy_value_means_ask(self):
        self.assertEqual(self.pack(package_policy='whatever').package_policy(), 'ask')
        self.assertEqual(self.pack(package_policy='allow').package_policy(), 'allow')

    def test_the_size_cap_has_a_floor_and_a_default(self):
        self.assertEqual(self.pack(env_max_mb='').env_max_mb(), env.DEFAULT_MAX_MB)
        self.assertEqual(self.pack(env_max_mb='abc').env_max_mb(), env.DEFAULT_MAX_MB)
        self.assertEqual(self.pack(env_max_mb='1').env_max_mb(), 50)
        self.assertEqual(self.pack(env_max_mb='800').env_max_mb(), 800)


class ToolingTests(EnvTestCase):
    def test_choosing_pytest_installs_it_with_the_environment_and_repairs_an_environment_without_it(self):
        # 真实情况：设置里测试框架是 pytest，换成项目环境后里面没有 pytest，Python 就“未就绪”了。创建（或再点一次）会补装它。
        # Real case: the framework setting is pytest, and after switching to the project environment it has no pytest, so Python became "not ready". Creating (or clicking again) installs it.
        pack = self.pack(interpreter='', test_framework='pytest')
        environment = pack.environment()
        fake_python(self.temp.name)
        with patch.object(env, 'create'), patch.object(env, 'top_levels', return_value=frozenset()), patch.object(env, 'install', return_value={'installed': ['pytest==8'], 'size_mb': 20}) as installer:
            self.assertEqual(pack.tooling_missing(), ['pytest'])
            environment.create()
            installer.assert_called_once_with(['pytest'], env.DEFAULT_MAX_MB)
        with patch.object(env, 'create'), patch.object(env, 'top_levels', return_value=frozenset({'pytest'})), patch.object(env, 'install') as installer:
            self.assertEqual(pack.tooling_missing(), [])
            environment.create()
            installer.assert_not_called()
        self.pack(interpreter='', test_framework='unittest')
        with patch.object(env, 'top_levels', return_value=frozenset()):
            self.assertEqual(registry.get('python').tooling_missing(), [])

    def test_the_readiness_message_points_at_the_settings_button_when_pytest_is_missing(self):
        pack = self.pack(interpreter='', test_framework='pytest')
        fake = fake_python(self.temp.name)
        with patch.object(PythonPackModule, '_version', return_value=((3, 12, 0), '3.12.0')), patch.object(env, 'env_python', return_value=fake),                 patch('masa.languages.python.pack.subprocess.run', return_value=types.SimpleNamespace(returncode=1, stdout='', stderr='')):
            ready = pack.probe()
        self.assertFalse(ready.ok)
        self.assertIn('补装 pytest', ready.details[0])


class InterpreterTests(EnvTestCase):
    def test_empty_setting_means_the_project_environment_once_it_exists(self):
        pack = self.pack(interpreter='')
        self.assertEqual(pack.interpreter(), Path(sys.executable))  # 还没有项目环境 / no project environment yet
        self.assertFalse(pack.uses_project_env())
        python = fake_python(self.temp.name)
        self.assertEqual(pack.interpreter(), python)
        self.assertTrue(pack.uses_project_env())

    def test_an_explicit_interpreter_always_wins(self):
        fake_python(self.temp.name)
        pack = self.pack(interpreter=sys.executable)
        self.assertEqual(pack.interpreter(), Path(sys.executable))
        self.assertFalse(pack.uses_project_env())


class DiscoveryTests(EnvTestCase):
    def test_the_store_placeholder_and_missing_files_are_not_offered(self):
        real = sys.executable
        stub = r'C:\Users\x\AppData\Local\Microsoft\WindowsApps\python3.EXE'
        with patch.object(env.shutil, 'which', side_effect=lambda name: stub if name == 'python3' else None), patch.object(env, '_run', side_effect=OSError):
            found = [item['path'] for item in env.discover_interpreters()]
        self.assertIn(real, found)
        self.assertNotIn(stub, found)
        self.assertEqual(len(found), len({os.path.normcase(p) for p in found}))


class AgentFacingTests(EnvTestCase):
    def test_installed_packages_are_let_through_by_the_static_check_and_named_in_the_notes(self):
        fake_python(self.temp.name)
        pack = self.pack(interpreter='', package_policy='ask')
        with patch.object(env, 'top_levels', return_value=frozenset({'numpy', 'sklearn'})):
            self.assertEqual(pack._extra_imports(), {'numpy', 'sklearn'})
            self.assertIn('numpy, sklearn', pack.packages_note())
            self.assertIn('numpy, sklearn', pack.worker_notes())
            self.assertIn('numpy, sklearn', pack.project_prompt({'purpose': 'project_developer'}, 'x'))
            pack.config['package_policy'] = 'deny'
            self.assertEqual(pack._extra_imports(), set())  # 禁止：已装的也不让用 / deny: even installed ones are not offered
            self.assertEqual(pack.packages_note(), '')

    def test_without_packages_the_prompts_are_untouched(self):
        pack = self.pack(interpreter='')
        self.assertEqual(pack.packages_note(), '')
        self.assertTrue(pack.worker_notes().endswith('(Windows writes CRLF). '))

    def test_a_third_party_requirement_is_a_risk_unless_the_policy_is_deny(self):
        goal = '写一个 CLI：用 numpy 计算输入数字的平均值并输出'
        self.pack(package_policy='deny')
        self.assertEqual(assess(goal, registry.get('python'))['verdict'], INFEASIBLE)
        self.pack(package_policy='ask')
        result = assess(goal, registry.get('python'))
        self.assertEqual(result['verdict'], 'risky')
        self.assertIn('third_party', {f['rule'] for f in result['findings']})

    def test_required_packages_names_what_is_missing(self):
        fake_python(self.temp.name)
        pack = self.pack(interpreter='')
        with patch.object(env, 'top_levels', return_value=frozenset({'numpy'})), patch.object(env, 'packages', return_value=[{'name': 'numpy', 'version': '1'}]):
            wanted = pack.required_packages('用 numpy 和 pandas 统计，再用 sklearn')
        self.assertEqual(wanted, {'named': ['numpy', 'pandas', 'scikit-learn'], 'missing': ['pandas', 'scikit-learn']})


class RealEnvironmentTests(EnvTestCase):
    def test_a_real_environment_can_be_created_inspected_and_reset(self):
        try:
            done = subprocess.run([sys.executable, '-c', 'import venv, ensurepip'], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest('venv unavailable')
        if done.returncode != 0:
            self.skipTest('venv unavailable')
        self.assertFalse(env.exists())
        status = env.create()
        self.assertTrue(status['exists'])
        self.assertEqual(status['packages'], [])  # pip 等基础设施不算项目依赖 / pip and friends are not project dependencies
        self.assertGreater(status['size_mb'], 1)
        self.assertEqual(env.top_levels(), frozenset())
        env.reset()
        self.assertTrue(env.exists())


class ConsoleTests(EnvTestCase):
    def setUp(self):
        super().setUp()
        from masa.application.console import Console
        self.console = Console(Path(self.temp.name) / 'state', Path(self.temp.name) / 'no-runner.exe', Path(self.temp.name) / 'no-go.exe', Path(__file__).resolve().parents[1])
        self.addCleanup(lambda: registry.configure(default='go', configs={}, extra_dirs=[]))

    def test_triage_reports_the_packages_the_goal_names(self):
        result = self.console.triage({'goal': '写一个 CLI：用 numpy 求平均值', 'language': 'python'})
        self.assertEqual(result['packages']['named'], ['numpy'])
        self.assertEqual(result['packages']['missing'], ['numpy'])
        self.assertEqual(result['packages']['policy'], 'ask')
        plain = self.console.triage({'goal': '写一个 CLI：输出 hello', 'language': 'python'})
        self.assertNotIn('packages', plain)
        go = self.console.triage({'goal': '写一个 CLI：输出 hello', 'language': 'go'})
        self.assertNotIn('packages', go)

    def test_install_and_env_endpoints_follow_the_pack_and_refuse_other_languages(self):
        with self.assertRaisesRegex(MasaError, 'no project environment'):
            self.console.project_env('go')
        status = self.console.project_env('python')
        self.assertFalse(status['exists'])
        self.assertIn(sys.executable, [c['path'] for c in status['candidates']])
        asked = self.console.project_env_install('python', {'packages': ['numpy']})
        self.assertTrue(asked['needs_approval'])
        with self.assertRaises(MasaError):
            self.console.project_env_install('python', {'packages': ['git+https://x/y.git'], 'approve': True})
        with self.assertRaises(MasaError):
            self.console.project_env_action('python', {'action': 'format-disk'})


if __name__ == '__main__':
    unittest.main()
