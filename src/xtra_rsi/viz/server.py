"""``rsi viz`` 的本地 HTTP 服务：只读，默认只监听 127.0.0.1（ADR 0008）。

- ``GET /``：面板（单文件 HTML，无外部依赖）
- ``GET /api/loops``：所有 loop 和节点的摘要（:func:`xtra_rsi.viz.data.snapshot`）
- ``GET /api/loop?id=<id>[&full=1]``：一个 loop 每轮每个阶段的摘要，full 时附全文

只读扫描时发现的目录，不接受任意路径。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import TYPE_CHECKING, override
from urllib.parse import parse_qs, urlparse

from xtra_rsi.viz.data import Found, discover, loop_detail, snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from xtra_rsi.payload import Payload

RESCAN_S = 5.0


class Catalog:
    """扫描结果，最多每 :data:`RESCAN_S` 秒重新扫描一次。"""

    def __init__(self, roots: list[Path]) -> None:
        """记下要扫描的目录。"""
        self.roots: list[Path] = roots
        self._found: Found = Found()
        self._at: float = 0.0
        self._lock: threading.Lock = threading.Lock()

    def get(self) -> Found:
        """当前的扫描结果。"""
        with self._lock:
            if time.monotonic() - self._at > RESCAN_S:
                self._found = discover(self.roots)
                self._at = time.monotonic()
            return self._found


def _index_html() -> bytes:
    return files("xtra_rsi.viz").joinpath("index.html").read_bytes()


def make_handler(catalog: Catalog) -> type[BaseHTTPRequestHandler]:
    """绑定了 catalog 的请求处理类。"""

    class Handler(BaseHTTPRequestHandler):
        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            _ = self.wfile.write(body)

        def _json(self, obj: Payload, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self._send(code, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in {"/", "/index.html"}:
                self._send(200, _index_html(), "text/html; charset=utf-8")
            elif url.path == "/api/loops":
                self._json(snapshot(catalog.get()))
            elif url.path == "/api/loop":
                lid = (q.get("id") or [""])[0]
                path = catalog.get().loops.get(lid)
                if path is None:
                    self._json({"error": f"没有这个 loop: {lid}"}, 404)
                    return
                self._json(loop_detail(lid, path, full=(q.get("full") or ["0"])[0] == "1"))
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def serve(roots: list[Path], *, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """创建服务（不启动）；调用方 ``serve_forever()``。"""
    server = ThreadingHTTPServer((host, port), make_handler(Catalog(roots)))
    server.daemon_threads = True
    return server
