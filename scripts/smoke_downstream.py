#!/usr/bin/env python3
"""端到端验证下游接入：构建 wheel，装进一个临时 uv 项目，跑 rsi sync / check / eval。

用法：``uv run python scripts/smoke_downstream.py``。成功退出码 0，进度写 stderr。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*argv: str, cwd: Path) -> str:
    """运行命令，失败时把输出带进异常。"""
    _ = sys.stderr.write(f"$ {' '.join(argv)}\n")
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode != 0:
        msg = f"{argv} 退出码 {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        raise RuntimeError(msg)
    return proc.stdout


def main() -> int:
    """构建、安装并检查。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        _ = run("uv", "build", "--wheel", "--out-dir", str(tmpdir / "dist"), cwd=ROOT)
        wheel = next((tmpdir / "dist").glob("xtra_rsi-*.whl"))
        proj = tmpdir / "downstream"
        _ = run(
            "uv", "init", "--quiet", "--no-workspace", "--name", "downstream", str(proj), cwd=tmpdir
        )
        _ = run("uv", "add", "--quiet", str(wheel), cwd=proj)
        _ = run("uv", "run", "rsi", "sync", cwd=proj)
        _ = run("uv", "run", "rsi", "check", "harness", cwd=proj)
        _ = run("uv", "run", "rsi", "sync", "--check", cwd=proj)
        # 下游要用自己的用例证明每个阶段有效；这里借用 toy profile 模拟
        case = '[{"name": "smoke", "profile": "toy", "metric": "final_score", '
        case += '"iterations": 15, "min_delta": 0.005}]\n'
        for cases in (proj / "harness").glob("*/evals"):
            _ = (cases / "cases.json").write_text(case, encoding="utf-8")
        _ = run("uv", "run", "rsi", "eval", cwd=proj)
        agents = (proj / "harness" / "observe" / "AGENTS.md").read_text(encoding="utf-8")
        if "# observe" not in agents or "xtra-rsi:generated" not in agents:
            _ = sys.stderr.write("生成的 AGENTS.md 内容不对\n")
            return 1
    _ = sys.stderr.write("ok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
