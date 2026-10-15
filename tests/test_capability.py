"""本机能否跑本地模型的轻量检测。 The light host check for local models."""
import unittest
from unittest.mock import patch

from masa.domain.models import MasaError
from masa.infrastructure import capability
from masa.infrastructure.capability import GIB, detect
from masa.infrastructure.llm import ChatProvider


def host(ram_gb, vram_gb, env=None):
    return detect(env=env or {}, ram=lambda: None if ram_gb is None else ram_gb * GIB, gpu=lambda: None if vram_gb is None else vram_gb * GIB)


class CapabilityTests(unittest.TestCase):
    def tearDown(self):
        capability.STATE.clear()
        capability.STATE.update(allowed=True, reason='not evaluated')

    def test_rule(self):
        """5070Ti(16G)+64G 放行；无显卡的云小机器、小显存、小内存都挡住。 Dev box passes; GPU-less cloud boxes, small VRAM and small RAM are blocked."""
        self.assertTrue(host(64, 16)['allowed'])
        self.assertTrue(host(16, 6)['allowed'])
        self.assertFalse(host(64, None)['allowed'])
        self.assertFalse(host(4, None)['allowed'])
        self.assertFalse(host(64, 4)['allowed'])
        self.assertFalse(host(8, 24)['allowed'])
        self.assertFalse(host(None, 24)['allowed'])

    def test_env_override(self):
        """环境变量可强制开/关。 The env var forces it on or off."""
        self.assertFalse(host(64, 16, {'MASA_LOCAL_MODELS': '0'})['allowed'])
        self.assertTrue(host(64, None, {'MASA_LOCAL_MODELS': '1'})['allowed'])

    def test_demo_and_enforcement(self):
        """演示部署强制禁用；禁用后 ChatProvider 拒绝本地配置，云端不受影响。 Demo forces it off; a disabled host refuses local providers but not cloud."""
        with patch.object(capability, 'detect', return_value={'allowed': True, 'reason': 'ok', 'vram_gb': 16, 'ram_gb': 64, 'mode': 'auto'}):
            self.assertTrue(capability.configure(demo=False)['allowed'])
            self.assertFalse(capability.configure(demo=True)['allowed'])
        local = {'model_type': 'local', 'base_url': 'http://127.0.0.1:11434', 'model': 'qwen'}
        with self.assertRaises(MasaError):
            ChatProvider(local)
        capability.STATE.update(allowed=True)
        ChatProvider(local)
        capability.STATE.update(allowed=False)
        ChatProvider({'model_type': 'cloud', 'base_url': 'https://api.example.com/v1', 'model': 'm'}, 'key')


if __name__ == '__main__':
    unittest.main()
