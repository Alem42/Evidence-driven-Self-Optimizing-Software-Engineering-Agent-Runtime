from masa.domain.proposals import validate_files, validate_file_proposal, validate_repair, validate_test_revision
"""已批准架构到多文件草稿和隔离执行。 Approved architecture to multi-file drafts and isolated execution."""
from pathlib import Path
from masa.infrastructure.proc import NO_WINDOW
import tempfile
import time
import json
import re
import subprocess
import os
from masa.domain.models import Budget, MasaError, canonical, stage_error
from masa.runtime.engine import Runtime
from masa.application.planning import ProjectPlanning, validate_spec
from masa.infrastructure.workspaces import verify_snapshot


from masa.application.checks.snippets import source_snippets  # noqa: E402
from masa.application.checks.goimports import fix_imports, fix_module_imports  # noqa: E402
from masa.application.checks.leaks import leak_messages  # noqa: E402
from masa.application.checks.testlint import test_problem_messages  # noqa: E402
from masa.application.checks.entrypoint import entry_problem_messages  # noqa: E402


def concise_failure_evidence(result, files=None):
    """保留测试输出前后文并提取关键编译/断言诊断。 Preserve bounded context and extract actionable compiler/assertion diagnostics."""
    stdout=result.get('stdout','')
    stderr=result.get('stderr','')
    diagnostics=[]
    markers=re.compile(r'(?i)(?:\.go:\d+|no Go files in |import cycle|FAIL|panic:|undefined:|imported and not used|expected|want |exit code|build failed)')
    for line in stdout.splitlines():
        try:
            frame=json.loads(line)
            value=frame.get('Output','') if isinstance(frame,dict) else ''
        except (ValueError,TypeError):
            value=line
        for item in str(value).splitlines():
            item=item.strip()
            if item and markers.search(item) and item not in diagnostics:
                diagnostics.append(item[:400])
    for line in stderr.splitlines():
        item=line.strip()
        if item and markers.search(item) and item not in diagnostics:
            diagnostics.append(item[:400])
    return {'diagnostics':diagnostics[:24],'snippets':source_snippets(diagnostics[:24],files),
            'stdout':stdout if len(stdout)<=8000 else stdout[:3000]+'\n...[middle omitted; see diagnostics]...\n'+stdout[-5000:],
            'stderr':stderr[-5000:],
            'output_may_be_truncated':len(stdout)>8000 or len(stderr)>5000 or bool(result.get('truncated'))}






# 每个文件语法错误就地重写的最多次数。 Max in-stage rewrites per file with a parse error.
SYNTAX_RETRIES = 2


class ProjectGeneration:
    def __init__(self, store, executor):
        """沿用运行账本与审批存储。 Reuse the run ledger and approval artifacts."""
        self.store, self.executor = store, executor
        self.planning = ProjectPlanning(store, executor)

    def _gofmt(self, files, paths, rid=None, imports=True):
        """用工具链 gofmt 规范指定的 Go 文件；失败（如语法错误）则保持原样，让真实编译检查暴露问题。
        Normalize the given Go files with the toolchain gofmt; on failure (e.g. syntax errors) keep them as-is.
        imports=True 时先做确定性的 import 修复（补缺失/删多余，见 goimports），不调用模型。
        With imports=True, deterministic import fixing (goimports.py) runs first, with no model call."""
        go = getattr(self.executor, 'go_executable', None)
        if not go:
            return files
        formatter = Path(go).parent / ('gofmt.exe' if os.name == 'nt' else 'gofmt')
        if not formatter.is_file():
            return files
        out = dict(files)
        fixed_imports = {}
        # 批准的 module 路径与本项目的包目录：用来修“module 前缀写错”的内部 import。 The approved module and this project's package directories.
        module_line = next((line.split(None, 1)[1].strip() for line in str(files.get('go.mod', '')).splitlines() if line.startswith('module ')), None)
        package_dirs = sorted({k.rsplit('/', 1)[0] for k in files if k.endswith('.go') and '/' in k and k.rsplit('/', 1)[0] != 'cmd/app'})
        for path in paths:
            if not path.endswith('.go') or not isinstance(files.get(path), str):
                continue
            if imports:
                source, changes = fix_imports(files[path])
                source, module_changes = fix_module_imports(source, module_line, package_dirs)
                changes = changes + module_changes
            else:
                source, changes = files[path], []
            try:
                process = subprocess.run([str(formatter)], input=source.encode('utf-8'),
                                         capture_output=True, timeout=10, check=False, **NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if process.returncode == 0 and 0 < len(process.stdout) <= 60000:
                out[path] = process.stdout.decode('utf-8')
                if changes:
                    fixed_imports[path] = [f'{kind} {name}' for kind, name in changes]
        if fixed_imports and rid:
            self.store.event(rid, 'imports_fixed', {'files': fixed_imports})
        return out

    @staticmethod
    def _refuse_leaks(changes, base=None):
        """修复/测试修订的产物里出现提示词片段就当作被拒绝（协调器会带原因重试或升级）。
        A repair or test revision that contains prompt text is rejected (the coordinator retries or escalates with the reason)."""
        # 入口规则只拦“新引入”的违规：原来的 main.go 已经读输入时，修复不该因为保留原样而被拒绝，否则已有的问题会把整个修复卡死。
        # The entrypoint rule only blocks NEWLY introduced violations: when the original main.go already reads input, a repair must not be refused for keeping it
        # (an existing problem would otherwise block the whole repair).
        entry = entry_problem_messages(changes)
        if base:
            already = set(entry_problem_messages(base))
            entry = {path: message for path, message in entry.items() if path not in already}
        leaked = {**leak_messages(changes), **entry}
        if leaked:
            raise MasaError('generated Go files contain harness prompt text (fix these first): ' + ' | '.join(f'{k}: {v}' for k, v in list(leaked.items())[:3]))

    def _syntax_errors(self, files, paths):
        """用 gofmt -e 做确定性语法检查（不需要编译整个项目），返回 {路径: 带行列号的错误}。工具链缺失时视为无错误。
        Deterministic parse check with `gofmt -e` (no project build needed): {path: error text with line:column}. Missing toolchain = no errors."""
        found = dict(leak_messages(files, paths))  # 提示词泄漏不需要工具链 / prompt leaks need no toolchain
        for path, message in {**test_problem_messages(files, paths), **entry_problem_messages(files, paths)}.items():  # 空转的测试、读输入的 main.go 同理 / so do vacuous tests and a main.go that reads input
            found[path] = (found[path] + chr(10) if path in found else '') + message
        go = getattr(self.executor, 'go_executable', None)
        if not go:
            return found
        formatter = Path(go).parent / ('gofmt.exe' if os.name == 'nt' else 'gofmt')
        if not formatter.is_file():
            return found
        for path in paths:
            if not path.endswith('.go') or not isinstance(files.get(path), str):
                continue
            try:
                process = subprocess.run([str(formatter), '-e'], input=files[path].encode('utf-8'),
                                         capture_output=True, timeout=10, check=False, **NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if process.returncode != 0:
                text = process.stderr.decode('utf-8', 'replace').replace('<standard input>', path)
                found[path] = (found[path] + chr(10) if path in found else '') + chr(10).join(text.strip().splitlines()[:5])[:600]
        return found

    def generate(self, parent, provider, on_created=None, resume_id=None, retry_feedback=None, test_provider=None, per_file=False):
        """只根据已批准规格生成独立草稿，不写实现文件。 Generate an independent draft from approved specifications only."""
        original = self.store.run(parent)
        plan = original['data'].get('project_plan', {})
        if plan.get('status') != 'approved' or plan.get('kind') == 'code' or original['cancel_requested']:
            raise MasaError('approve the project specification first')
        approved = self.store.read(plan['approval_ref'])
        graph = Runtime.compile_project_checks(approved['spec'], approved['checks'])
        if resume_id:
            existing=self.store.run(resume_id)
            metadata=existing['data'].get('project_plan',{})
            if existing['data'].get('parent_run_id')!=parent or metadata.get('status')!='generating' or metadata.get('repair_of'):
                raise MasaError('only interrupted generation can resume')
            rid=resume_id
        else:
            metadata = {'kind':'code','status':'generating','spec_approval_ref':plan['approval_ref'], 'provider':provider.profile}
            # 本地模型的输出预算较小：新草稿逐文件生成，旧草稿仍沿用原调用契约。
            # Local models have smaller output budgets: new drafts use file calls, old drafts keep their original contract.
            config = getattr(provider, 'config', {}) or {}
            snapshot = getattr(provider, 'snapshot', {}) or {}
            frozen_config = snapshot.get('config', {}) if isinstance(snapshot, dict) else {}
            local_files = (config.get('model_type') == 'local'
                           and frozen_config.get('protocol', snapshot.get('protocol')) == 'ollama')
            # 云端默认一次生成全部文件（省调用）；一旦输出被截断（项目大、超过单次输出上限），就改成逐文件生成：
            # 每次只写一个文件，不再受整包输出上限约束（评测里 L9 的 regex 因此死在生成阶段）。
            # Cloud models generate everything in one call by default (fewer calls); once the output is cut off (a big project over the single-call limit)
            # switch to one file per call so the whole-bundle limit no longer applies (a benchmark L9 task died at generation for exactly this).
            local_files = local_files or bool(per_file)
            if local_files:
                metadata['generation_mode'] = 'files-v1'
            calls = (len(approved['spec']['files']) - 1) * (1 + SYNTAX_RETRIES) if local_files else 2
            rid = Runtime(self.store, self.executor).create(Path(original['data']['workspace']), original['data']['goal'],
                  Budget(model_calls=calls, tool_calls=3, deadline_seconds=86400), graph=graph,
                  parent_run_id=parent, project_plan=metadata)
        if on_created:
            on_created(rid)
        try:
            if metadata.get('generation_mode') == 'files-v1':
                files = self._generate_local_files(rid, provider, original['data']['goal'], approved, metadata, retry_feedback, test_provider)
            else:
                values = {'goal':original['data']['goal'], **approved}
                if retry_feedback:values['previous_attempt_error'] = str(retry_feedback)[:1000]
                files = self.planning.call(rid, provider, 'project_developer', values)
            # 模型常写出不符合 gofmt 的代码；格式属于确定性工作，不应消耗修复调用。
            # Models often emit non-gofmt code; formatting is deterministic and must not cost a repair call.
            files = self._gofmt(files, list(files), rid)
            # 语法错误的草稿不值得验证：直接让本阶段带精确诊断重试（云端单次生成走协调器的阶段重试/升级）。
            # A draft that does not even parse is not worth verifying: fail the stage with exact diagnostics so it is retried/escalated.
            broken = self._syntax_errors(files, list(files))
            if broken:
                raise MasaError('generated Go files do not parse (fix these first): ' + ' | '.join(f'{k}: {v}' for k, v in list(broken.items())[:3]))
            validate_files(files, approved['spec'])
            metadata.update(status='awaiting_review', files_ref=self.store.put(files))
            self.planning.update(rid, metadata, 'paused', 'project_code_review_requested')
            if metadata.get('status') in {'cancelled', 'failed'}:
                raise MasaError(metadata.get('error', 'project generation cancelled'))
            return rid
        except Exception as exc:
            metadata.update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'generation failed')
            self.planning.update(rid, metadata, 'failed', 'project_code_generation_failed')
            raise stage_error(exc, f'project generation failed; run {rid}: {metadata["error"]}') from None

    def _generate_local_files(self, rid, provider, goal, approved, metadata, retry_feedback=None, test_provider=None):
        """逐文件落盘与恢复，不重放未知调用；合并后仍进行完整校验。 Checkpoint file calls without replaying unknown results; validate the final bundle."""
        from masa.runtime.roles import RoleRuntime
        spec = approved['spec']
        validate_spec(spec)
        # go.mod 由已批准规格确定，无需浪费模型调用；实现先于测试。
        # The approved specification determines go.mod; generate implementation before tests.
        files = {'go.mod': f"module {spec['module']}\n\ngo 1.27.0\n"}
        paths = [item['path'] for item in spec['files'] if item['path'] != 'go.mod']
        impl_paths = [path for path in paths if not path.endswith('_test.go')]
        test_paths = [path for path in paths if path.endswith('_test.go')]
        # 有“测试作者”模型（更强的等级）时测试先行：它定义公共接口，较弱的模型对着冻结的测试写实现；否则保持“实现先于测试”。
        # With a stronger test author the tests come FIRST and define the public API; the weaker model implements against the frozen tests.
        paths = test_paths + impl_paths if test_provider is not None else impl_paths + test_paths
        roles = RoleRuntime(self.store)
        for ordinal, path in enumerate(paths):
            metadata['gen_progress'] = {'completed': ordinal, 'total': len(paths), 'current': path}
            self.planning.update(rid, metadata, 'created', 'project_file_generation_started')
            if metadata.get('status') in {'cancelled', 'failed'}:
                raise MasaError(metadata.get('error', 'project generation cancelled'))
            values = {'goal': goal, **approved, 'generation_mode': 'files-v1',
                      'target_path': path, 'previous_files': dict(files)}
            if retry_feedback:values['previous_attempt_error'] = str(retry_feedback)[:1000]
            invocation = 'initial' if ordinal == 0 else f'file:{ordinal + 1}'
            author = test_provider if (test_provider is not None and path.endswith('_test.go')) else provider
            slot = {'freeze_key': 'tests_snapshot_ref'} if author is not provider else {}
            generated = roles.call(rid, author, 'project_developer', values, invocation_id=invocation, **slot)
            validate_file_proposal(generated, spec, path)
            # 模型刚写完、上下文还在：语法错误就地带行列号重写（本地免费、确定性），不留给验证阶段去“修实现”。
            # Right after the write, with context still warm: rewrite on a parse error using line:column (free locally, deterministic).
            for retry in range(1, SYNTAX_RETRIES + 1):
                candidate = self._gofmt({**files, **generated}, [path], rid)
                broken = self._syntax_errors(candidate, [path])
                if not broken:
                    break
                again = dict(values, previous_attempt_error='The file you just wrote was rejected by deterministic checks (syntax, harness text, or tests that cannot fail). Fix exactly these problems and return the complete file again:' + chr(10) + broken[path])
                generated = roles.call(rid, author, 'project_developer', again, invocation_id=f'{invocation}:syntax{retry}', **slot)
                validate_file_proposal(generated, spec, path)
            files.update(generated)
            metadata['partial_files_ref'] = self.store.put(files)
            metadata['gen_progress'] = {'completed': ordinal + 1, 'total': len(paths), 'current': None}
            self.planning.update(rid, metadata, 'created', 'project_file_generated')
            if metadata.get('status') in {'cancelled', 'failed'}:
                raise MasaError(metadata.get('error', 'project generation cancelled'))
        return validate_files(files, spec)

    def repair(self, parent, provider, feedback='', on_created=None, use_intelligence=False):
        """以真实失败证据生成一次修复草稿，不自动批准或循环。 Propose one evidence-backed repair without auto-approval or loops."""
        original=self.store.run(parent)
        data=original['data']
        if original['status']!='failed' or not data.get('project_bundle'):
            raise MasaError('repair requires a failed generated-project verification')
        if not isinstance(feedback,str) or len(feedback)>4000:
            raise MasaError('feedback must be at most 4000 characters')
        verify_snapshot(Path(data['workspace']),data['snapshot_id'])
        base_ref=data['project_bundle']['approval_ref']
        base=self.store.read(base_ref)
        approved=self.store.read(base['spec_approval_ref'])
        evidence=[]
        for call in self.store.tools(parent):
            if not call['result_ref']:continue
            result=self.store.read(call['result_ref'])
            evidence.append({'operation':self.store.read(call['request_ref'])['operation'],
                'result_ref':call['result_ref'],'status':result['status'],'exit_code':result.get('exit_code'),
                **concise_failure_evidence(result,base['files'])})
        if not any(e['status']=='completed' and e['exit_code']!=0 for e in evidence):
            raise MasaError('repair requires a recorded failed check; unknown results need inspection')
        metadata={'kind':'code','status':'generating','spec_approval_ref':base['spec_approval_ref'],
                  'base_approval_ref':base_ref,'repair_of':parent,'provider':provider.profile}
        rid=Runtime(self.store,self.executor).create(Path(data['workspace']),data['goal'],
            Budget(model_calls=1,tool_calls=3,deadline_seconds=86400),
            graph=Runtime.compile_project_checks(approved['spec'],approved['checks']),
            parent_run_id=parent,project_plan=metadata)
        if on_created:on_created(rid)
        try:
            original_files=base['files']
            context_report=None
            if use_intelligence:
                from masa.intelligence.repair_context import build_repair_context
                original_files,context_report=build_repair_context(self.store,self.executor,rid,base['files'],evidence,feedback)
            changes=self.planning.call(rid,provider,'project_repair',{
                'goal':data['goal'],**approved,'original_files':original_files,
                'failure_evidence':evidence,'feedback':feedback,'context_selection':context_report})
            if any(path not in original_files for path in changes):
                raise MasaError('repair changed a file outside supplied context; request a broader revision')
            self._refuse_leaks(changes,base['files'])
            files=validate_repair(changes,base['files'])
            # 冻结的测试与 go.mod 不动；其余 Go 文件统一格式化，修复不再被 gofmt 卡住。
            # Frozen tests/go.mod stay untouched; format every other Go file so repairs are never blocked by gofmt.
            files=self._gofmt(files,[p for p in files if p!='go.mod' and not p.endswith('_test.go')],rid)
            validate_files(files,approved['spec'])
            metadata.update(status='awaiting_review',files_ref=self.store.put(files),
                            changed_files=[p for p in files if files[p]!=base['files'][p]])
            self.planning.update(rid,metadata,'paused','project_repair_review_requested')
            return rid
        except Exception as exc:
            metadata.update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'repair generation failed')
            self.planning.update(rid,metadata,'failed','project_repair_failed')
            raise stage_error(exc, f'repair failed; run {rid}: {metadata["error"]}') from None

    def resume_revision(self, rid, provider):
        """重新校验已保存的修复结果，不重复请求模型。 Revalidate a saved revision without replaying model requests."""
        from masa.runtime.roles import RoleRuntime
        run=self.store.run(rid)
        metadata=run['data'].get('project_plan',{})
        if not metadata.get('repair_of') or metadata.get('provider')!=provider.profile:
            raise MasaError('resume requires a revision and its original provider')
        if run['cancel_requested'] or time.time()>=run['data']['deadline_at']:
            raise MasaError('role run cancelled or deadline expired')
        if metadata.get('status') in {'awaiting_review','approved'}:
            return rid
        if metadata.get('status')!='generating':
            raise MasaError('only interrupted revision generation can resume')
        roles=RoleRuntime(self.store)
        purpose='project_test_revision' if metadata.get('revision_scope')=='tests' else 'project_repair'
        saved=next((row for row in roles.states(rid) if row['purpose']==purpose),None)
        # 免费本地模型的未完成调用可由 RoleRuntime 作为新尝试重试；其余仍要求显式新修订。
        # A free local call may be re-attempted by RoleRuntime; anything else still needs an explicit new revision.
        retryable=bool(saved) and saved['status']!='completed' and getattr(provider,'retry_unknown_calls',False) \
            and (getattr(provider,'config',None) or {}).get('model_type')=='local'
        if not saved or (saved['status']!='completed' and not retryable):
            raise MasaError('revision result unavailable; create an explicit retry revision')
        # 使用原始输入和原始模型配置校验缓存身份，恢复时仍执行所有提案约束。
        # Check cached input/route identity and repeat every proposal validation on recovery.
        context=dict(self.store.read(saved['input_ref']));context.pop('purpose')
        changes=roles.call(rid,provider,purpose,context)
        base=self.store.read(metadata['base_approval_ref'])
        approved=self.store.read(metadata['spec_approval_ref'])
        if purpose=='project_repair' and any(path not in context['original_files'] for path in changes):
            raise MasaError('repair changed a file outside supplied context; request a broader revision')
        validator=validate_test_revision if purpose=='project_test_revision' else validate_repair
        self._refuse_leaks(changes,base['files'])
        files=validator(changes,base['files'])
        tests_scope=purpose=='project_test_revision'
        files=self._gofmt(files,[p for p in files if (p.endswith('_test.go') if tests_scope else p!='go.mod' and not p.endswith('_test.go'))],rid)
        validate_files(files,approved['spec'])
        metadata.update(status='awaiting_review',files_ref=self.store.put(files),
                        changed_files=[p for p in files if files[p]!=base['files'][p]])
        self.planning.update(rid,metadata,'paused',purpose+'_review_requested')
        return rid

    def revise_tests(self, parent, provider, feedback='', on_created=None):
        """失败证据表明测试本身有缺陷时建立新版本。 Revise existing tests in a new evidence-linked version."""
        original=self.store.run(parent)
        data=original['data']
        if original['status']!='failed' or not data.get('project_bundle'):
            raise MasaError('test revision requires a failed generated-project verification')
        if not isinstance(feedback,str) or len(feedback)>4000:
            raise MasaError('feedback must be at most 4000 characters')
        verify_snapshot(Path(data['workspace']),data['snapshot_id'])
        base_ref=data['project_bundle']['approval_ref']
        base=self.store.read(base_ref)
        approved=self.store.read(base['spec_approval_ref'])
        evidence=[]
        for call in self.store.tools(parent):
            if not call['result_ref']:continue
            result=self.store.read(call['result_ref'])
            evidence.append({'operation':self.store.read(call['request_ref'])['operation'],
                'result_ref':call['result_ref'],'status':result['status'],'exit_code':result.get('exit_code'),
                **concise_failure_evidence(result,base['files'])})
        if not any(e['status']=='completed' and e['exit_code']!=0 for e in evidence):
            raise MasaError('test revision requires a recorded failed check')
        metadata={'kind':'code','status':'generating','spec_approval_ref':base['spec_approval_ref'],
                  'base_approval_ref':base_ref,'repair_of':parent,'revision_scope':'tests','provider':provider.profile}
        rid=Runtime(self.store,self.executor).create(Path(data['workspace']),data['goal'],
            Budget(model_calls=1,tool_calls=3,deadline_seconds=86400),
            graph=Runtime.compile_project_checks(approved['spec'],approved['checks']),
            parent_run_id=parent,project_plan=metadata)
        if on_created:on_created(rid)
        try:
            changes=self.planning.call(rid,provider,'project_test_revision',{
                'goal':data['goal'],**approved,'original_files':base['files'],
                'failure_evidence':evidence,'feedback':feedback})
            self._refuse_leaks(changes,base['files'])
            files=validate_test_revision(changes,base['files'])
            files=self._gofmt(files,[p for p in files if p.endswith('_test.go')],rid)
            validate_files(files,approved['spec'])
            metadata.update(status='awaiting_review',files_ref=self.store.put(files),
                            changed_files=[p for p in files if files[p]!=base['files'][p]])
            self.planning.update(rid,metadata,'paused','project_test_revision_review_requested')
            return rid
        except Exception as exc:
            metadata.update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'test revision generation failed')
            self.planning.update(rid,metadata,'failed','project_test_revision_failed')
            raise stage_error(exc, f'test revision failed; run {rid}: {metadata["error"]}') from None

    def format_test_files(self, parent, on_created=None):
        """仅格式检查失败时无模型地修订测试快照。 Format test-only failures deterministically in a new reviewable snapshot."""
        original=self.store.run(parent)
        data=original['data']
        if original['status']!='failed' or not data.get('project_bundle'):
            raise MasaError('format revision requires a failed generated-project verification')
        verify_snapshot(Path(data['workspace']),data['snapshot_id'])
        base_ref=data['project_bundle']['approval_ref']
        base=self.store.read(base_ref)
        approved=self.store.read(base['spec_approval_ref'])
        format_paths=[]
        for call in self.store.tools(parent):
            if not call['result_ref']:continue
            operation=self.store.read(call['request_ref'])['operation']
            result=self.store.read(call['result_ref'])
            if result['status']!='completed':
                raise MasaError('format revision requires completed check evidence')
            if result['exit_code']==0:continue
            if operation!='go_fmt_check':
                raise MasaError('format revision requires all other checks to pass')
            format_paths=[p.strip().replace('\\','/') for p in result.get('stdout','').splitlines() if p.strip()]
        if not format_paths or any(p not in base['files'] for p in format_paths):
            raise MasaError('format revision requires existing files')
        # 全是测试文件走测试修订范围，全是实现文件走实现范围；混合需要人来处理。
        # Test-only paths use the test scope, source-only paths the implementation scope; mixed needs a human.
        tests = [p.endswith('_test.go') for p in format_paths]
        if any(tests) and not all(tests):
            raise MasaError('format revision requires either only test files or only implementation files')
        scope = 'tests' if all(tests) else 'implementation'
        formatter=self.executor.go_executable.parent / ('gofmt.exe' if os.name=='nt' else 'gofmt')
        if not formatter.is_file():
            raise MasaError('gofmt binary missing')
        changes={}
        for path in format_paths:
            try:
                process=subprocess.run([str(formatter)],input=base['files'][path].encode('utf-8'),
                                       capture_output=True,timeout=10,check=False,**NO_WINDOW)
            except (OSError,subprocess.TimeoutExpired):
                raise MasaError('gofmt did not complete') from None
            if process.returncode!=0 or len(process.stdout)>60000:
                raise MasaError('gofmt rejected test file')
            changes[path]=process.stdout.decode('utf-8')
        files=validate_test_revision(changes,base['files']) if scope=='tests' else validate_repair(changes,base['files'])
        validate_files(files,approved['spec'])
        if all(files[p]==base['files'][p] for p in format_paths):
            raise MasaError('gofmt made no changes')
        metadata={'kind':'code','status':'awaiting_review','spec_approval_ref':base['spec_approval_ref'],
                  'base_approval_ref':base_ref,'repair_of':parent,'revision_scope':scope,
                  'format_only':True,'changed_files':format_paths,'files_ref':self.store.put(files)}
        rid=Runtime(self.store,self.executor).create(Path(data['workspace']),data['goal'],
            Budget(model_calls=1,tool_calls=3,deadline_seconds=86400),
            graph=Runtime.compile_project_checks(approved['spec'],approved['checks']),
            parent_run_id=parent,project_plan=metadata)
        if on_created:on_created(rid)
        self.store.event(rid,'test_format_applied',{'files_ref':metadata['files_ref'],'paths':format_paths})
        self.planning.update(rid,metadata,'paused','project_test_format_review_requested')
        return rid

    def reverify(self, interrupted):
        """被中断的验证（进程崩溃时工具正在运行）在恢复后会被引擎保守地标为 needs_attention 且不重放工具。
        检查工具在隔离副本里运行、结果幂等，所以对同一份已发布快照新建一个验证 run 是安全的（与手动“重新验证”相同）。
        An interrupted verification is conservatively marked needs_attention and never replayed in place. The check tools are
        idempotent and run in an isolated copy, so a NEW verification run of the same published snapshot is safe."""
        original = self.store.run(interrupted)
        data = original['data']
        if original['status'] != 'needs_attention' or not data.get('project_bundle'):
            raise MasaError('re-verification requires an interrupted generated-project verification')
        if not str(original['reason']).startswith('uncertain_tool_state'):
            raise MasaError('only an interrupted tool state can be re-verified automatically')
        verify_snapshot(Path(data['workspace']), data['snapshot_id'])
        from masa.domain.models import Graph
        new_id = Runtime(self.store, self.executor).create(Path(data['workspace']), data['goal'], Budget(tool_calls=3, deadline_seconds=1800),
                                                           graph=Graph.from_dict(data['graph']), parent_run_id=interrupted,
                                                           project_bundle=data['project_bundle'])
        if self.store.run(new_id)['data']['snapshot_id'] != data['snapshot_id']:
            self.store.set_status(new_id, 'needs_attention', 'snapshot_mismatch')
            raise MasaError('snapshot changed while preparing re-verification')
        return new_id

    def approve(self, rid, body):
        """先准备完整目录再创建可运行快照；重试复用已发布运行。 Prepare all files before publishing a run; retries reuse it."""
        run = self.store.run(rid)
        plan = run['data'].get('project_plan', {})
        if time.time() >= run['data']['deadline_at']:
            raise MasaError('code review deadline expired; generate a new draft')
        if run['status'] != 'paused' or run['cancel_requested'] or plan.get('kind') != 'code' or plan.get('status') not in {'awaiting_review','approved'}:
            raise MasaError('code draft is not awaiting approval')
        if body.get('files_ref') != plan.get('files_ref'):
            raise MasaError('stale code draft')
        spec = self.store.read(plan['spec_approval_ref'])
        files = validate_files(body.get('files'), spec['spec'])
        if plan.get('base_approval_ref'):
            base=self.store.read(plan['base_approval_ref'])['files']
            scope=plan.get('revision_scope','implementation')
            if scope=='tests':
                if any(files[p]!=content for p,content in base.items() if not p.endswith('_test.go')):
                    raise MasaError('test revision cannot change implementation or module')
            elif any(files[p]!=content for p,content in base.items() if p=='go.mod' or p.endswith('_test.go')):
                raise MasaError('repair approval cannot change frozen tests or module')
        approval_ref = self.store.put({'files':files, 'spec_approval_ref':plan['spec_approval_ref']})
        if plan.get('approval_ref') and plan['approval_ref'] != approval_ref:
            raise MasaError('approved files cannot be changed; generate a new draft')
        # 先锁定审批内容，再发布快照；中断后只允许重试同一批准版本。
        # Freeze approved content before publishing; recovery can only retry this revision.
        plan.update(approval_ref=approval_ref, status='approved')
        if body.get('review_mode')=='automatic':plan['review_mode']='automatic'
        self.planning.update(rid, plan, 'paused', 'project_code_approved')
        published=self.store.published_child(rid,approval_ref)
        if published:
            return published
        # 临时准备目录不会暴露为成功项目，Runtime 复制完毕才发布运行记录。
        # Staging is never a published project; Runtime publishes only after copying a complete snapshot.
        with tempfile.TemporaryDirectory(prefix='project-', dir=self.store.root) as temp:
            source = Path(temp)
            for name, content in files.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8', newline='\n')
            child = Runtime(self.store,self.executor).create(source,run['data']['goal'],
                    Budget(tool_calls=3,deadline_seconds=1800),
                    graph=Runtime.compile_project_checks(spec['spec'],spec['checks']),parent_run_id=rid,
                    project_bundle={'approval_ref':approval_ref})
        return child
