"""本地 Ollama 控制边界，使用固定端点而不是任意 shell。 Local Ollama controls through fixed endpoints, never arbitrary shells."""
import json
import urllib.error
import urllib.request
from masa.domain.models import MasaError
from masa.infrastructure.llm import NoRedirect


class OllamaControl:
    def __init__(self):
        """只管理本机默认服务。 Control the default loopback service only."""
        self.base='http://127.0.0.1:11434'

    def request(self,path,body=None,timeout=10):
        """限制响应大小、超时和重定向，不自动重试。 Bound responses/timeouts and refuse redirects or retries."""
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
                                       headers={'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise MasaError('Ollama response too large')
                value=json.loads(raw)
                if not isinstance(value,dict):raise MasaError('invalid Ollama response')
                return value
        except (OSError,ValueError,urllib.error.URLError):
            raise MasaError('Ollama request failed; check the local service; no automatic retry') from None

    def catalog(self):
        """合并磁盘列表和当前内存占用。 Combine installed models and actual loaded-memory metadata."""
        return {'base_url':self.base,'version':self.request('/api/version').get('version'),
                'models':self.request('/api/tags').get('models',[]),'running':self.request('/api/ps').get('models',[])}

    def installed(self,model):
        """动作仅接受已安装的完整名称，不触发隐式下载。 Accept installed full tags only; never implicitly download."""
        if not isinstance(model,str) or not model or len(model)>200:
            raise MasaError('invalid local model name')
        for item in self.request('/api/tags').get('models',[]):
            if item.get('name')==model:return item
        raise MasaError('local model is not installed; refresh Ollama models')

    def show(self,model):
        """返回模型信息，屏蔽庞大的模板与许可证正文。 Expose details without huge templates/license bodies."""
        self.installed(model)
        raw=self.request('/api/show',{'model':model})
        return {k:raw.get(k) for k in ('details','model_info','capabilities','parameters','modified_at')}

    def residency(self,model,load):
        """空生成请求加载/释放模型；不执行代码或shell。 Load/unload with an empty generation, never code or shells."""
        self.installed(model)
        return self.request('/api/generate',{'model':model,'stream':False,'keep_alive':'5m' if load else 0},timeout=600)
