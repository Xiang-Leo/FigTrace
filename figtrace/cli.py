import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="FigTrace browser workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    roots = os.getenv("FIGTRACE_ALLOWED_ROOTS")
    allowed = (
        [Path(p) for p in roots.split(os.pathsep) if p] if roots is not None else None
    )
    local_mode = args.host in ("127.0.0.1", "localhost", "::1")
    if not local_mode and (not os.getenv("FIGTRACE_PASSWORD") or not allowed):
        parser.error(
            "远程服务需设置 FIGTRACE_PASSWORD 和 FIGTRACE_ALLOWED_ROOTS，并通过 FIGTRACE_HOSTS 指定访问主机名"
        )
    app = create_app(args.data_dir, allowed_roots=allowed, local_mode=local_mode)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
