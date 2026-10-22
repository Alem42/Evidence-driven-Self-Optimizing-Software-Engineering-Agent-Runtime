"""控制台的语言与项目环境部分：语言包状态/配置、Python 项目环境、解释器选择。 The console's language and project-environment part."""
from masa.domain.models import MasaError
from masa.languages.registry import registry


class LanguageMixin:
    def languages(self):
        """每个语言包的说明、就绪状态与当前配置（设置页用）。 Every pack's description, readiness and current configuration (for the Settings page)."""
        return {'default': registry.default_id(), 'languages': registry.describe(self._probe_executor()), 'extra_dirs': self.settings.load_languages().get('extra_dirs', [])}

    # ───────────── Python 项目环境（设置 → 语言 → Python）/ the Python project environment ─────────────
    def _environment(self, language):
        environment = registry.get(language).environment() if language in registry.ids() else None
        if environment is None:
            raise MasaError('this language has no project environment')
        return environment

    def project_env(self, language='python'):
        """项目环境的状态、策略、本机能选的其它解释器。 Status of the project environment, the policy and the other interpreters that can be chosen."""
        return self._environment(language).status()

    def project_env_action(self, language, body):
        action = body.get('action')
        if action not in ('create', 'reset'):
            raise MasaError('action must be create or reset')
        environment = self._environment(language)
        with self.lock:
            (environment.reset if action == 'reset' else environment.create)()
        return environment.status()

    def project_env_install(self, language, body):
        """装第三方包到项目环境。策略 ask：没有 approve 就只返回“需要批准”；allow：直接装；deny：拒绝。你在设置页手动点“安装”本身就是批准。
        Install third-party packages into the project environment. Policy ask: without approve only "needs approval" comes back; allow installs at once; deny refuses. Clicking Install in Settings is the approval."""
        environment = self._environment(language)
        with self.lock:
            done = environment.install(body.get('packages'), approve=body.get('approve') is True)
        return done if done.get('needs_approval') else {**done, 'env': environment.status()}

    def pick_file(self, body):
        """在运行 masa 的这台电脑上弹出系统“打开文件”对话框（设置页的“浏览…”）。 Opens the system file dialog on the machine running masa (the Settings page's Browse button)."""
        return {'path': self._environment(body.get('language') or 'python').pick_interpreter()}

    def save_languages(self, body):
        self.settings.save_languages(body)
        return self.languages()

    def _language_of(self, body):
        """任务的语言：请求里的优先，否则默认语言；必须已注册且工具链就绪。 The task's language: the request's, else the default; it must be registered and its toolchain ready."""
        language = body.get('language') or registry.default_id()
        pack = registry.get(language)
        # 没有运行器时（测试/开发替身）不做就绪检查，真正的错误会在执行时明确报出 / without a runner (a test or dev harness) readiness is not checked; the real error is reported at execution
        ready = pack.probe(self._probe_executor()) if self.runner_path.is_file() else None
        if ready is not None and not ready.ok:
            raise MasaError(f'language {pack.title} is not ready: ' + '; '.join(ready.details))
        return pack.id

    def _probe_executor(self):
        """给语言包探测工具链用的执行器；运行器不存在时给一个空对象（探测会如实说缺什么）。 The executor language packs probe their toolchains with; an empty object when there is no runner (the probe then says what is missing)."""
        import types
        try:
            return self._runner()
        except MasaError:
            return types.SimpleNamespace()
