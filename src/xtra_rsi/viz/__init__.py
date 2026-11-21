"""``rsi viz``：在可缩放的画布上看所有 RSI loop 的目标、当前阶段、每轮结果（ADR 0008）。"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from xtra_rsi.viz.server import serve

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["main", "serve"]


def main(roots: list[Path], *, host: str = "127.0.0.1", port: int = 8765) -> int:
    """启动服务直到 Ctrl-C。下游的命令（例如 ``kbench viz``）也调用它。"""
    server = serve(roots, host=host, port=port)
    real_port = server.server_address[1]
    scanned = ", ".join(map(str, roots))
    _ = sys.stderr.write(f"rsi viz: http://{host}:{real_port}/  （扫描 {scanned}）\n")
    _ = sys.stderr.write(
        f"远程机器上运行时，在本地执行 ssh -L {real_port}:localhost:{real_port} <host> 再打开\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
