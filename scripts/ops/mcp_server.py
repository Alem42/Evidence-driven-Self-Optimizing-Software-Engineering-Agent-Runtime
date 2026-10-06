"""启动 MCP 服务（stdio）。接入方法见 docs/guides/MCP.md。 Start the MCP server (stdio); see docs/guides/MCP.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from masa.interfaces.mcp_server import main  # noqa: E402

if __name__ == '__main__':
    main()
