"""多文件发布与重试边界。 Multi-file publication and retry boundaries."""
import tempfile
import unittest
from pathlib import Path
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.domain.models import MasaError
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration, validate_files, validate_repair, concise_failure_evidence
from masa.domain.proposals import validate_test_revision
from masa.runtime.engine import Runtime
from test_project_plan import SPEC, CHECKS, PlannerProvider
from test_runtime import FakeExecutor

FILES = {'go.mod':'module example.com/task\n\ngo 1.27.0\n',
         'cmd/app/main.go':'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hello") }\n',
         'internal/app/app.go':'package app\n\nfunc Value() int { return 42 }\n',
         'internal/app/app_test.go':'package app\n\nimport "testing"\n\nfunc TestValue(t *testing.T) {\n\tif Value() != 42 {\n\t\tt.Fatal("wrong value")\n\t}\n}\n'}


class DeveloperProvider(PlannerProvider):
    def respond(self, context):
        assert context['purpose'] == 'project_developer'
        return dict(FILES)


class ProjectGenerationTests(unittest.TestCase):
    def test_format_only_test_revision_preserves_assertions_and_passes_real_go_checks(self):
        """真实 Go 格式失败可零模型调用修复且原断言保留。 Repair a real Go format failure with no model call or assertion change."""
        root=Path(__file__).resolve().parents[1]
        go=root/'.tools/go/bin/go.exe';binary=root/'.tools/bin/masa-runner.exe'
        if not go.is_file() or not binary.is_file():self.skipTest('built Go runner unavailable')
        unformatted={**FILES,'internal/app/app_test.go':
            'package app\nimport "testing"\nfunc TestValue(t *testing.T){if Value()!=42{t.Fatal("wrong value")}}\n'}
        class UnformattedProvider(DeveloperProvider):
            def respond(self,context):return dict(unformatted)
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));runner=Runner(binary,go)
            try:
                planning=ProjectPlanning(store,runner)
                parent=planning.generate(PlannerProvider(),'Build a small CLI')
                p=store.run(parent)['data']['project_plan']
                planning.approve(parent,{'spec_ref':p['spec_ref'],'checks_ref':p['checks_ref'],'spec':SPEC,'checks':CHECKS})
                service=ProjectGeneration(store,runner)
                draft=service.generate(parent,UnformattedProvider())
                p=store.run(draft)['data']['project_plan']
                original=service.approve(draft,{'files_ref':p['files_ref'],'files':unformatted})
                self.assertEqual(Runtime(store,runner).execute(original)['status'],'failed')
                revised=service.format_test_files(original)
                p=store.run(revised)['data']['project_plan']
                files=store.read(p['files_ref'])
                self.assertIn('Value() != 42',files['internal/app/app_test.go'])
                self.assertEqual(store.run(revised)['model_calls'],0)
                verified=service.approve(revised,{'files_ref':p['files_ref'],'files':files})
                self.assertEqual(Runtime(store,runner).execute(verified)['status'],'succeeded')
                self.assertEqual(store.run(original)['status'],'failed')
            finally:store.close()

    def test_repair_context_keeps_early_compile_error_amid_long_test_output(self):
        """长日志末尾不能淹没首部编译错误。 A long test tail must not hide an early compiler error."""
        import json
        first=json.dumps({'Output':'cmd/app/main.go:15:40: undefined: io\n'})+'\n'
        tail=''.join(json.dumps({'Output':f'=== RUN TestCase{i}\n'})+'\n' for i in range(250))
        evidence=concise_failure_evidence({'stdout':first+tail,'stderr':'','truncated':False})
        self.assertIn('undefined: io',' '.join(evidence['diagnostics']))
        self.assertLess(len(evidence['stdout']),8500)
        self.assertTrue(evidence['output_may_be_truncated'])

    def test_test_revision_is_separate_from_implementation_repair(self):
        """测试修订不能改实现，原修复仍冻结测试。 Test revision cannot edit implementation; repair still freezes tests."""
        changed='package app\n\nimport "testing"\nfunc TestValue(t *testing.T) { if Value()!=42 {t.Fatal("wrong")} }\n'
        self.assertEqual(validate_test_revision({'internal/app/app_test.go':changed},FILES)['internal/app/app_test.go'],changed)
        for path in ('go.mod','internal/app/app.go','new_test.go'):
            with self.assertRaises(MasaError):validate_test_revision({path:changed},FILES)
        with self.assertRaises(MasaError):validate_repair({'internal/app/app_test.go':changed},FILES)

    def test_approved_spec_to_complete_snapshot_and_idempotent_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)); executor=FakeExecutor()
            try:
                planning=ProjectPlanning(store,executor)
                parent=planning.generate(PlannerProvider(),'Build a small CLI')
                service=ProjectGeneration(store,executor)
                with self.assertRaises(MasaError): service.generate(parent,DeveloperProvider())
                plan=store.run(parent)['data']['project_plan']
                planning.approve(parent,{'spec_ref':plan['spec_ref'],'checks_ref':plan['checks_ref'],'spec':SPEC,'checks':CHECKS})
                draft=service.generate(parent,DeveloperProvider())
                metadata=store.run(draft)['data']['project_plan']
                self.assertFalse((Path(store.run(draft)['data']['workspace'])/'cmd').exists())
                with self.assertRaises(MasaError): Runtime(store,executor).execute(draft)
                body={'files_ref':metadata['files_ref'],'files':FILES}
                child=service.approve(draft,body)
                self.assertEqual(service.approve(draft,body),child)
                data=store.run(child)['data']
                for name,content in FILES.items():
                    self.assertEqual((Path(data['workspace'])/name).read_text(),content)
                self.assertEqual(Runtime(store,executor).execute(child)['status'],'succeeded')
                self.assertEqual(store.run(child)['model_calls'],0)
                changed={**FILES,'internal/app/app.go':'package app\n'}
                with self.assertRaises(MasaError): service.approve(draft,{**body,'files':changed})
            finally: store.close()

    def test_reject_extra_missing_dependencies_and_limits(self):
        for files in [{**FILES,'../evil.go':'evil'}, {k:v for k,v in FILES.items() if k!='go.mod'},
                      {**FILES,'go.mod':'module evil\n'}, {**FILES,'internal/app/app.go':'a'*60001}]:
            with self.subTest(files=list(files)):
                with self.assertRaises(MasaError): validate_files(files,SPEC)

    def test_repair_preserves_tests_and_requires_real_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));executor=FakeExecutor(exit_code=1)
            try:
                planning=ProjectPlanning(store,executor)
                parent=planning.generate(PlannerProvider(),'Build CLI')
                plan=store.run(parent)['data']['project_plan']
                planning.approve(parent,{'spec_ref':plan['spec_ref'],'checks_ref':plan['checks_ref'],'spec':SPEC,'checks':CHECKS})
                service=ProjectGeneration(store,executor);draft=service.generate(parent,DeveloperProvider())
                meta=store.run(draft)['data']['project_plan']
                child=service.approve(draft,{'files_ref':meta['files_ref'],'files':FILES})
                with self.assertRaises(MasaError):service.repair(child,DeveloperProvider())
                Runtime(store,executor).execute(child)
                class RepairProvider:
                    profile={'provider':'test','model':'fake'}
                    usage={'total_tokens':1}
                    def respond(self,context):
                        self.context=context
                        return {'internal/app/app.go':'package app\n\nfunc Value() int { return 43 }\n'}
                provider=RepairProvider();repair=service.repair(child,provider)
                self.assertEqual(provider.context['purpose'],'project_repair')
                self.assertTrue(provider.context['failure_evidence'])
                meta=store.run(repair)['data']['project_plan'];files=store.read(meta['files_ref'])
                self.assertEqual(files['internal/app/app_test.go'],FILES['internal/app/app_test.go'])
                edited={**files,'internal/app/app_test.go':'package app\n'}
                with self.assertRaises(MasaError):service.approve(repair,{'files_ref':meta['files_ref'],'files':edited})
                self.assertEqual(store.run(child)['status'],'failed')
                self.assertEqual(store.run(repair)['tool_calls'],0)
            finally:store.close()
        for forbidden in ['go.mod','internal/app/app_test.go','new.go']:
            with self.assertRaises(MasaError):validate_repair({forbidden:'changed'},FILES)
