from __future__ import annotations

import os
import sys

# Running as `python path/to/goofish_agent/main.py` puts this package directory
# first on sys.path, so bare `import platform` (e.g. from SQLAlchemy) resolves to
# our `goofish_agent/platform/` package instead of the stdlib.
_script_dir = os.path.dirname(os.path.abspath(__file__))
if sys.path and os.path.realpath(sys.path[0]) == os.path.realpath(_script_dir):
    sys.path.pop(0)
    _parent = os.path.dirname(_script_dir)
    if os.path.realpath(_parent) not in {
        os.path.realpath(p) for p in sys.path
    }:
        sys.path.insert(0, _parent)

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="二手平台买家 Agent · Web 服务入口（打开浏览器访问页面使用）"
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--no-reload", action="store_true", help="关闭自动重载")
    args = parser.parse_args()
    run_server(args.host, args.port, reload=not args.no_reload)


def run_server(host: str, port: int, reload: bool = True) -> None:
    import uvicorn

    from goofish_agent.config.logging import setup_logging

    setup_logging()
    print(f"\n  Goofish Agent Web UI → http://localhost:{port}/\n")
    uvicorn.run(
        "goofish_agent.api.app:app", host=host, port=port, reload=reload
    )


if __name__ == "__main__":
    main()
