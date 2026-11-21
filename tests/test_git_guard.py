"""Tests for .claude/hooks/git_guard.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "git_guard.py"


class GitGuard(Protocol):
    """The parts of git_guard the tests use (.claude/hooks is not an importable package)."""

    def check(
        self, command: str, cwd: str, inherited: set[str] | None = None, *, strict: bool = True
    ) -> str | None: ...

    def strip_redirections(self, command: str) -> tuple[str, list[str]]: ...

    def clean_env(self) -> dict[str, str]: ...


def _load_guard() -> GitGuard:
    spec = importlib.util.spec_from_file_location("git_guard", HOOK)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("GitGuard", cast("object", module))


guard = _load_guard()


def make_repo(root: Path, name: str, branch: str) -> Path:
    path = root / name
    _ = subprocess.run(  # noqa: S603 -- fixed git argv in a test repo
        ["git", "init", "-q", "-b", branch, str(path)],  # noqa: S607 -- git from PATH
        check=True,
        env=guard.clean_env(),
    )
    return path


def git(path: str, *args: str) -> None:
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    _ = subprocess.run(  # noqa: S603 -- fixed git argv in a test repo
        ["git", "-C", path, *ident, *args],  # noqa: S607 -- git from PATH
        check=True,
        env=guard.clean_env(),
    )


def run_hook(stdin: str) -> str:
    r = subprocess.run(  # noqa: S603 -- runs the hook under test with this interpreter
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, check=False
    )
    return r.stdout


@dataclass(frozen=True)
class Repos:
    """A repo on main and a repo on `feature` (with a.py committed and an `other` branch)."""

    main: str
    feat: str

    def cwd(self, where: str) -> str:
        return self.main if where == "main" else self.feat

    def denied(self, cmd: str, where: str) -> bool:
        """Whether the guard denies `cmd` ({main}/{feat} filled in) run in repo `where`."""
        return guard.check(cmd.format(main=self.main, feat=self.feat), self.cwd(where)) is not None


@pytest.fixture(scope="module")
def repos(tmp_path_factory: pytest.TempPathFactory) -> Repos:
    root = tmp_path_factory.mktemp("git_guard")
    main = str(make_repo(root, "repo", "main"))
    feat = str(make_repo(root, "repo-feature", "feature"))
    # feature repo with history: a tracked file and a second branch
    _ = (Path(feat) / "a.py").write_text("x = 1\n")
    git(feat, "add", "a.py")
    git(feat, "commit", "-qm", "init")
    git(feat, "branch", "other")
    return Repos(main, feat)


# Each case is (command, repo it runs in). `{main}` / `{feat}` become the repo paths.

BRANCH_SWITCHING_DENIED = [
    ("git switch main", "feat"),
    ("git checkout main", "feat"),
    ("git checkout other", "feat"),
    ("git checkout -b foo", "feat"),
    ("git checkout -", "feat"),
    ("git checkout --track origin/x", "feat"),
]
BRANCH_SWITCHING_ALLOWED = [
    ("git checkout -- a.py", "feat"),
    ("git checkout .", "feat"),
    ("git checkout a.py", "feat"),
    ("git checkout HEAD -- a.py", "feat"),
    ("git checkout other a.py", "feat"),
    ("git checkout -p a.py", "feat"),
    ("git worktree add ../x -b x", "feat"),
]

COMMIT_DIR_DENIED = [
    # commit uses the session cwd
    ("git commit -m x", "main"),
    # commit follows cd
    ("cd {main} && git commit -m x", "feat"),
    ("cd ../repo && git commit -m x", "feat"),
    # commit follows -C
    ("git -C {main} commit -m x", "feat"),
    ("git -c user.name=x -C {main} commit -m x", "feat"),
]
COMMIT_DIR_ALLOWED = [
    ("git commit -m x", "feat"),
    ("cd {feat} && git add . && git commit -m x", "main"),
    ("git -C {feat} commit -m x", "main"),
]

COMMIT_CREATING = ["git merge feature", "git cherry-pick abc", "git revert abc", "git am x.patch"]
COMMIT_ON_MAIN_DENIED = [
    *((cmd, "main") for cmd in COMMIT_CREATING),
    # --skip goes on with the rest of the sequence, which creates commits
    ("git cherry-pick --skip", "main"),
    ("git am --skip", "main"),
    ("git revert --skip", "main"),
    # -n is --no-stat for merge, which still commits
    ("git merge -n feature", "main"),
]
COMMIT_ON_MAIN_ALLOWED = [
    *((cmd, "feat") for cmd in COMMIT_CREATING),
    ("git merge --ff-only origin/main", "main"),
    ("git merge --abort", "main"),
    ("git pull", "main"),
    ("git merge --no-commit feature", "main"),
    ("git merge --squash feature", "main"),
    ("git cherry-pick -n abc", "main"),
    ("git revert --no-commit abc", "main"),
    ("git am --show-current-patch=diff", "main"),
]

HEREDOC_TEXT = "cat > notes.md <<'EOF'\nDon't forget\nEOF\n"
HEREDOC_DENIED = [
    (HEREDOC_TEXT + "git commit -am x", "main"),
    (HEREDOC_TEXT + "git switch main", "feat"),
    ("git commit -F - <<EOF\nDon't\nEOF", "main"),
    ("cat <<-EOF\n\tit's\n\tEOF\ngit commit -m x", "main"),
    # the body of a heredoc fed to a shell is a script
    ("bash <<'EOF'\ngit push origin main\nEOF", "feat"),
    # unparseable and mentions git: denied
    ("echo it's && git commit -m x", "feat"),
]
HEREDOC_ALLOWED = [
    (HEREDOC_TEXT + "git commit -am x", "feat"),
    (HEREDOC_TEXT + "git status", "main"),
    # unparseable without git: left to the shell
    ("echo it's", "feat"),
]

SUBSHELL_DENIED = [
    ("(cd {main} && git commit -m x)", "feat"),
    ("(cd {main}&&git commit -m x)", "feat"),
    ("(cd {feat}) && git commit -m x", "main"),
    ("echo $(git push origin main)", "feat"),
]
SUBSHELL_ALLOWED = [("(cd {main}) && git commit -m x", "feat")]

WRAPPERS_DENIED = [
    ("env SKIP=review-gate git push", "feat"),
    ("env -u FOO SKIP=review-gate git push", "feat"),
    ("bash -c 'git push origin main'", "feat"),
    ("sh -lc 'git commit --no-verify -m x'", "feat"),
    ("bash -c 'cd {main} && git commit -m x'", "feat"),
    ("command git push origin main", "feat"),
    ("nohup git push origin main", "feat"),
    ("env git push origin main", "feat"),
    ("SKIP=review-gate bash -c 'git push'", "feat"),
    ("env SKIP=review-gate sh -c 'git push -u origin feature'", "feat"),
    ("time -p git push origin main", "feat"),
    ("nohup -- git push origin main", "feat"),
    ("command -p git push origin main", "feat"),
    ("env -C {main} git commit -m x", "feat"),
    ("env --chdir={main} git commit -m x", "feat"),
    ("pushd {main} && git commit -m x", "feat"),
]
WRAPPERS_ALLOWED = [
    ("env FOO=1 git push", "feat"),
    ("FOO=1 bash -c 'git push'", "feat"),
    ("bash -c 'git status'", "main"),
    ("env -C {feat} git commit -m x", "main"),
    ("pushd {main} && popd && git commit -m x", "feat"),
]

ALIASES_DENIED = [
    ("git -c alias.ci=commit ci -m x", "main"),
    ("git -c alias.p='push origin main' p", "feat"),
    ("git -c 'alias.p=!git push origin main' p", "feat"),
    ("git -c alias.ci='commit --no-verify' ci -m x", "feat"),
]
ALIASES_ALLOWED = [("git -c alias.ci=commit ci -m x", "feat")]

PUSH_TO_HEAD = ["git push origin HEAD", "git push origin @", "git push -u origin +HEAD"]
PUSH_DENIED = [
    ("git push", "main"),
    ("git push origin main", "feat"),
    ("git push origin HEAD:main", "feat"),
    ("git -C {main} push", "feat"),
    *((cmd, "main") for cmd in PUSH_TO_HEAD),
]
PUSH_ALLOWED = [
    *((cmd, "feat") for cmd in PUSH_TO_HEAD),
    ("git push -u origin feature", "main"),
    ("git push", "feat"),
    ("cd {feat} && git push", "main"),
]

NON_GIT_ALLOWED = [("git status", "main"), ("echo 'git commit -m x'", "main")]

REDIRECTIONS_DENIED = [
    ("git push origin main 2>&1", "feat"),
    ("git push 2>&1 | tail", "main"),
    ("git commit -m x >/dev/null", "main"),
]

HOOK_BYPASS_DENIED = [
    ("git push --no-verify", "feat"),
    ("SKIP=review-gate git push -u origin feature", "feat"),
    ("git commit --no-verify -m x", "feat"),
    ("git commit -nm x", "feat"),
    ("git -c core.hooksPath=/dev/null push", "feat"),
    ("git config core.hooksPath /tmp/none", "feat"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git push", "feat"),
    ("GIT_CONFIG_PARAMETERS=\"'core.hookspath'='/x'\" git commit -m x", "feat"),
]
HOOK_BYPASS_ALLOWED = [
    ("git push -n origin feature", "feat"),
    ("git commit -am x", "feat"),
    ("git grep hooksPath", "feat"),
]

MERGE_API_DENIED = [("gh api -X PUT repos/o/r/pulls/3/merge", "main")]
MERGE_API_ALLOWED = [("gh api repos/o/r/pulls/3", "main")]

# ADR 0007: only a human approves or rejects a sub-RSI
RSI_APPROVAL_DENIED = [
    ("rsi approve n0001", "feat"),
    ("rsi reject n0001 --reason x", "feat"),
    ("uv run rsi approve n0001", "feat"),
    ("uv run --frozen rsi approve n0001", "feat"),
    ("uv run --with foo rsi approve n0001", "feat"),
    ("uv run -p 3.12 rsi reject n0001", "feat"),
    ("uv run rsi --nodes-dir x approve n0001", "feat"),
    (".venv/bin/rsi approve n0001", "feat"),
    ("python -m xtra_rsi.cli approve n0001", "feat"),
    ("python3 -m xtra_rsi.cli reject n0001", "feat"),
    ("cd {feat} && uv run rsi approve n0001", "main"),
    ("bash -c 'uv run rsi approve n0001'", "feat"),
    ("env FOO=1 uv run rsi approve n0001", "feat"),
]
RSI_APPROVAL_ALLOWED = [
    ("uv run rsi node list", "feat"),
    ("uv run rsi node show n0001", "feat"),
    ("uv run rsi run --profile toy --node root", "feat"),
    ("uv run pytest -k approve", "feat"),
    ("echo rsi approve", "feat"),
]

DENIED = [
    *BRANCH_SWITCHING_DENIED,
    *COMMIT_DIR_DENIED,
    *COMMIT_ON_MAIN_DENIED,
    *HEREDOC_DENIED,
    *SUBSHELL_DENIED,
    *WRAPPERS_DENIED,
    *ALIASES_DENIED,
    *PUSH_DENIED,
    *REDIRECTIONS_DENIED,
    *HOOK_BYPASS_DENIED,
    *MERGE_API_DENIED,
    *RSI_APPROVAL_DENIED,
]
ALLOWED = [
    *BRANCH_SWITCHING_ALLOWED,
    *COMMIT_DIR_ALLOWED,
    *COMMIT_ON_MAIN_ALLOWED,
    *HEREDOC_ALLOWED,
    *SUBSHELL_ALLOWED,
    *WRAPPERS_ALLOWED,
    *ALIASES_ALLOWED,
    *PUSH_ALLOWED,
    *NON_GIT_ALLOWED,
    *HOOK_BYPASS_ALLOWED,
    *MERGE_API_ALLOWED,
    *RSI_APPROVAL_ALLOWED,
]


@pytest.mark.parametrize(("cmd", "where"), DENIED)
def test_denied(repos: Repos, cmd: str, where: str) -> None:
    assert repos.denied(cmd, where)


@pytest.mark.parametrize(("cmd", "where"), ALLOWED)
def test_allowed(repos: Repos, cmd: str, where: str) -> None:
    assert not repos.denied(cmd, where)


def test_alias_from_git_config(repos: Repos) -> None:
    git(repos.feat, "config", "alias.sw", "switch")
    try:
        assert repos.denied("git sw main", "feat")
    finally:
        git(repos.feat, "config", "--unset", "alias.sw")


@pytest.mark.parametrize(
    ("cmd", "words"),
    [
        ("a 2>&1 | b", ["a", "|", "b"]),
        ("a >out <in b", ["a", "b"]),
        ("a x2>f", ["a", "x2"]),  # not an fd: stdout redirect
        ("a '2>x' \"3>y\"", ["a", "'2>x'", '"3>y"']),
        ("a > 'my log' b", ["a", "b"]),
        ("a && b", ["a", "&&", "b"]),
    ],
)
def test_strip_redirections(cmd: str, words: list[str]) -> None:
    assert guard.strip_redirections(cmd)[0].split() == words


class PrChecks:
    """Stand-in for git_guard.pr_checks: records the args and exits with `code`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch: pytest.MonkeyPatch = monkeypatch
        self.calls: list[list[str]] = []

    def exits(self, code: int) -> None:
        def run(args: list[str], _cwd: str) -> tuple[int, str]:
            self.calls.append(args)
            return code, "out"

        fake: Callable[[list[str], str], tuple[int, str]] = run
        self.monkeypatch.setattr(guard, "pr_checks", fake)


def test_pr_merge_requires_green_checks(repos: Repos, monkeypatch: pytest.MonkeyPatch) -> None:
    checks = PrChecks(monkeypatch)
    checks.exits(0)
    assert not repos.denied("gh pr merge 3 --squash", "main")
    assert not repos.denied("gh pr merge -R o/r 3 -t title --squash", "main")
    assert checks.calls[-1] == ["3", "-R", "o/r"]
    checks.exits(8)  # pending
    assert repos.denied("gh pr merge 3 --squash", "main")
    checks.exits(1)  # failed / no checks
    assert repos.denied("gh pr merge --squash --delete-branch", "main")
    assert checks.calls[-1] == []


@pytest.mark.parametrize(
    ("cmd", "selector"),
    [
        ("gh pr merge 6 --squash 2>&1 | tail -5", ["6"]),
        ("gh pr merge 6 >/dev/null 2>&1", ["6"]),
        ("gh pr merge 6 &> log", ["6"]),
        ("gh pr merge 2>err.txt 6", ["6"]),
        ("gh pr merge 7 --squash > out", ["7"]),
    ],
)
def test_pr_merge_ignores_redirections(
    repos: Repos, monkeypatch: pytest.MonkeyPatch, cmd: str, selector: list[str]
) -> None:
    checks = PrChecks(monkeypatch)
    checks.exits(0)
    assert not repos.denied(cmd, "main")
    assert checks.calls[-1] == selector


@pytest.mark.parametrize(
    "stdin", ["not json", "", "[]", '{"tool_input": null}', '{"tool_input": {}}']
)
def test_bad_input_fails_closed(stdin: str) -> None:
    assert '"deny"' in run_hook(stdin)


def test_hook_output(repos: Repos) -> None:
    ok = json.dumps({"tool_input": {"command": "git status"}, "cwd": repos.feat})
    assert run_hook(ok) == ""
    bad = json.dumps({"tool_input": {"command": "git switch x"}, "cwd": repos.feat})
    assert '"deny"' in run_hook(bad)
