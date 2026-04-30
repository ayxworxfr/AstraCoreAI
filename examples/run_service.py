"""启动 AstraCore AI 服务。

等价于：uvicorn astracore.service.api.app:create_app --factory --port 8000

用法：
    python examples/run_service.py              # 默认 0.0.0.0:8000
    python examples/run_service.py --port 8080
    python examples/run_service.py --reload     # 开发模式（文件变更自动重载）
"""

import argparse
import logging

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.getLogger("astracore").setLevel(logging.INFO)


def test_main() -> None:
    parser = argparse.ArgumentParser(description="启动 AstraCore AI 服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式：文件变更自动重载")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    uvicorn.run(
        "astracore.service.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    test_main()
