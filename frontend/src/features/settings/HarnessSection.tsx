import { useActivity } from '../../app/activity';
import { API_BASE } from '../../api/client';
import { Badge, Card } from '../../shared/ui';

// Harness：目前展示后端真实能力标记；检查策略、工具链版本等配置在后端提供接口后扩展。
// Harness: shows real backend capability flags; check policy / toolchain controls extend here once the API exists.
export function HarnessSection() {
  const { bootstrap, online } = useActivity();
  return (
    <div className="stack">
      <Card title="运行环境">
        <dl className="facts">
          <dt>后端 API</dt><dd><code>{API_BASE}</code> <Badge tone={online ? 'ok' : 'bad'} dot>{online ? '已连接' : '未连接'}</Badge></dd>
          <dt>Console 版本</dt><dd>{bootstrap?.console_version ?? '—'}</dd>
          <dt>Go runner</dt><dd><Badge tone={bootstrap?.runner_ready ? 'ok' : 'bad'} dot>{bootstrap?.runner_ready ? '就绪' : '未就绪'}</Badge></dd>
        </dl>
      </Card>
      <Card title="后端能力" subtitle="来自 /api/bootstrap 的真实标记。未实现的能力显示为关闭，不做假状态。">
        <div className="cap-grid">
          {Object.entries(bootstrap?.capabilities ?? {}).map(([k, v]) => (
            <div key={k} className="cap"><code>{k}</code><Badge tone={v ? 'ok' : 'neutral'}>{v ? '开' : '关'}</Badge></div>
          ))}
        </div>
      </Card>
      <Card title="检查策略（预留）" subtitle="未来在此配置检查白名单、超时、语言/工具链（Go 之外的项目）。需要后端提供配置接口。" />
    </div>
  );
}
