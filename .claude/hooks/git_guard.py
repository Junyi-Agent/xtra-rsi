#!/usr/bin/env python3
"""PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.

Denies switching branches in place, creating commits on main (commit, merge, cherry-pick,
revert, am), pushing to main, skipping the git hooks (--no-verify, SKIP=, core.hooksPath,
including through GIT_CONFIG_* variables), and merging a PR whose CI checks have not all
passed (`gh pr merge`, or the merge endpoint through `gh api`).

The branch is read from the directory each git command actually runs in: `cd <dir>`,
`pushd`/`popd` earlier in the same command, `( ... )` subshells, `env -C <dir>` and
`git -C <dir>` are all followed. Wrappers are looked through: `env VAR=x`, `command`,
`exec`, `nohup`, `time` (with their options), and `bash -c '...'`, which also inherits
the `VAR=x` words in front of it. Git aliases are expanded. Heredoc bodies are checked as
scripts of their own. If the hook input cannot be read, the check itself fails, or a
command that mentions git/gh cannot be parsed, the command is denied.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

PROTECTED = "main"
PUNCT = ";&|()"
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GIT_WORD = re.compile(r"\b(git|gh)\b")

# Long messages are triple-quoted with `\` line continuations: ruff (ISC003) forbids `+`
# between literals and basedpyright forbids implicit concatenation.
SWITCH_MSG = """\
Do not switch branches in this directory. Create a worktree outside the repo: \
git worktree add ../<repo>-<feature> -b <feature> origin/main (see CLAUDE.md). \
To restore files, use `git checkout -- <path>` or `git restore <path>`.\
"""
COMMIT_MSG = """\
Do not create commits on main (commit, merge, cherry-pick, revert, am). Work in a \
worktree on a feature branch and open a PR (see CLAUDE.md). To update main, use \
`git pull --ff-only` or `git merge --ff-only`.\
"""
PUSH_MSG = "Do not push to main. Push a feature branch and open a PR (see CLAUDE.md)."
BYPASS_MSG = """\
Do not skip the git hooks (--no-verify, SKIP=, core.hooksPath): pre-commit and the \
pre-push review gate must run. Fix what they report instead (see CLAUDE.md).\
"""
MERGE_API_MSG = "Merge PRs with `gh pr merge` so the CI check gate applies (see CLAUDE.md)."
PARSE_MSG = """\
git_guard could not parse this command (unbalanced quotes), and it mentions git/gh, so \
it is denied. Split it into simpler commands, or write long text to a file first.\
"""
CHECKS_MSG = """\
Do not merge: the PR's CI checks have not all passed (gh pr checks exit {code}). \
Wait for CI or fix it, then merge.
{output}"""
ERROR_MSG = """\
git_guard could not check this command ({name}: {error}), so it is \
denied. Fix .claude/hooks/git_guard.py or the hook input.\
"""
# Commands that create commits on the current branch, and per command the options that
# make it not do so. `--skip` is not here: it goes on with the rest of the sequence.
NO_COMMIT_OPTS = {
    "commit": {"--dry-run"},
    "merge": {"--ff-only", "--abort", "--quit", "--no-commit", "--squash"},
    "cherry-pick": {"--abort", "--quit", "-n", "--no-commit"},
    "revert": {"--abort", "--quit", "-n", "--no-commit"},
    "am": {"--abort", "--quit", "--show-current-patch"},
}
# `git checkout` options that create or switch to a branch.
SWITCH_OPTS = {"-b", "-B", "--orphan", "--detach", "-t", "--track"}
# Prefixes that run the rest of the words as a command, and their options that take a value.
WRAPPERS = {"command", "exec", "nohup", "time", "builtin"}
WRAPPER_VALUE_OPTS = {"-a", "-f", "-o", "--format", "--output"}
ENV_VALUE_OPTS = {"-u", "--unset"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Git does not let an alias shadow a built-in command, so these are never looked up.
GIT_BUILTINS = {
    "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file",
    "check-ignore", "checkout", "cherry", "cherry-pick", "clean", "clone", "commit",
    "config", "count-objects", "describe", "diff", "fetch", "for-each-ref", "format-patch",
    "fsck", "gc", "grep", "hash-object", "help", "init", "log", "ls-files", "ls-remote",
    "ls-tree", "merge", "merge-base", "mv", "name-rev", "notes", "pull", "push",
    "range-diff", "rebase", "reflog", "remote", "repack", "replace", "reset", "restore",
    "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-ref",
    "sparse-checkout", "stash", "status", "submodule", "switch", "symbolic-ref", "tag",
    "update-index", "update-ref", "var", "verify-commit", "version", "worktree",
}  # fmt: skip
# `gh pr merge` options that take a value; everything else non-dash is the PR selector.
GH_VALUE_OPTS = {
    "-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file",
    "-A", "--author-email", "--match-head-commit",
}  # fmt: skip


REDIRECT_OPS = ("&>>", "&>", "<<<", "<<-", "<<", ">>", ">&", ">|", "<&", "<>", ">", "<")
WORD_END = set(" \t\n;&|<>()")


def _copy_quoted(command: str, i: int, quote: str, out: list[str]) -> tuple[int, str]:
    """Copy the character at `i` inside `quote`; return the next index and the open quote."""
    c = command[i]
    out.append(c)
    if c == "\\" and quote == '"' and i + 1 < len(command):
        out.append(command[i + 1])
        i += 1
    elif c == quote:
        quote = ""
    return i + 1, quote


def _skip_redirect(command: str, i: int, op: str) -> tuple[int, str]:
    """Skip the operator `op` at `i` and its target; return the next index and the target."""
    n = len(command)
    i += len(op)
    while i < n and command[i] in " \t":
        i += 1
    start = i
    target_quote = ""
    while i < n and (target_quote or command[i] not in WORD_END):
        ch = command[i]
        if target_quote:
            target_quote = "" if ch == target_quote else target_quote
        elif ch in "'\"":
            target_quote = ch
        elif ch == "\\":
            i += 1
        i += 1
    return i, command[start:i]


def _read_heredocs(command: str, i: int, pending: list[tuple[str, bool]]) -> tuple[int, list[str]]:
    """Read the heredoc bodies starting at `i`; return the next index and the bodies."""
    n = len(command)
    bodies: list[str] = []
    for delim, strip_tabs in pending:
        lines: list[str] = []
        while i < n:
            j = command.find("\n", i)
            j = n if j < 0 else j
            line = command[i:j]
            i = j + 1
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
            lines.append(line)
        bodies.append("\n".join(lines))
    return i, bodies


def strip_redirections(command: str) -> tuple[str, list[str]]:
    """Drop shell redirections (`2>&1`, `> log`, `<<EOF`) outside quotes, with their targets.

    Without this, `gh pr merge 6 2>&1` would pass `2>` to `gh pr checks` as the PR selector.
    A leading fd number counts only when it is the whole unquoted word, as in the shell.
    Heredoc bodies are cut out of the command too, and returned separately: they are data
    for the command (where an apostrophe is fine), or a script when fed to a shell.
    """
    out: list[str] = []
    bodies: list[str] = []
    pending: list[tuple[str, bool]] = []  # heredoc delimiters waiting for the end of line
    quote = ""
    word_start, word_quoted = 0, False
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            i, quote = _copy_quoted(command, i, quote, out)
            continue
        if c == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            word_quoted = True
            i += 2
            continue
        if c in "'\"":
            quote, word_quoted = c, True
            out.append(c)
            i += 1
            continue
        op = next((o for o in REDIRECT_OPS if command.startswith(o, i)), "")
        if op and not command.startswith("(", i + len(op)):  # `<(..)` is process substitution
            word = "".join(out[word_start:])
            if word.isdigit() and not word_quoted:
                del out[word_start:]  # fd number, e.g. the 2 in 2>&1
            i, target = _skip_redirect(command, i, op)
            if op in ("<<", "<<-"):
                delim = re.sub(r"[\"'\\]", "", target)
                pending.append((delim, op == "<<-"))
            out.append(" ")
            word_start, word_quoted = len(out), False
            continue
        out.append(c)
        i += 1
        if c == "\n" and pending:
            i, read = _read_heredocs(command, i, pending)
            bodies += read
            pending.clear()
        if c in WORD_END:
            word_start, word_quoted = len(out), False
    return "".join(out), bodies


def segments(command: str) -> tuple[list[list[str]], list[str]]:
    """Split a command line into simple commands; also return its heredoc bodies.

    `(` and `)` come back as one-word segments so the caller can scope `cd` to a subshell.
    """
    command, bodies = strip_redirections(command)
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=PUNCT)
    lexer.whitespace_split = True
    out: list[list[str]] = [[]]
    for tok in lexer:
        if not tok or any(c not in PUNCT for c in tok):
            out[-1].append(tok)
            continue
        for c in tok:  # a punctuation run such as `)&&` or `((`
            if c in "()":
                out += [[c], []]
            elif out[-1]:
                out.append([])
    return [s for s in out if s], bodies


def env_flags(assignment: str) -> set[str]:
    """Hook-skipping effects of one `VAR=value` word: `skip` (SKIP=) or `hookspath`."""
    name, _, value = assignment.partition("=")
    flags: set[str] = set()
    if name == "SKIP":
        flags.add("skip")
    if name.startswith("GIT_CONFIG") and "hookspath" in value.lower():
        flags.add("hookspath")
    return flags


def _skip_env_opts(seg: list[str], cwd: str) -> tuple[list[str], str]:
    """Drop the options after `env`; return the rest and the directory `-C` sets."""
    while seg and seg[0].startswith("-"):
        opt = seg[0]
        if opt in ("-C", "--chdir") and len(seg) > 1:
            cwd = resolve(cwd, seg[1])
            seg = seg[2:]
        elif opt.startswith("--chdir="):
            cwd = resolve(cwd, opt.split("=", 1)[1])
            seg = seg[1:]
        else:
            seg = seg[2:] if opt in ENV_VALUE_OPTS else seg[1:]
    return seg, cwd


def unwrap(seg: list[str], cwd: str) -> tuple[list[str], set[str], str]:
    """Drop leading `VAR=x` words and wrappers.

    Returns the command, the hook-skipping flags its environment sets (see env_flags),
    and the directory it runs in (`env -C <dir>` changes it for this command only).
    """
    flags: set[str] = set()
    while seg:
        word = seg[0]
        if ASSIGNMENT.match(word):
            flags |= env_flags(word)
            seg = seg[1:]
        elif word == "env":
            seg, cwd = _skip_env_opts(seg[1:], cwd)
        elif word in WRAPPERS:
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                seg = seg[2:] if seg[0] in WRAPPER_VALUE_OPTS else seg[1:]
        else:
            break
    return seg, flags, cwd


def shell_script(seg: list[str]) -> str | None:
    """Return the script of `bash -c '<script>'` (also `sh`, `zsh`, `-lc`, `-ec` ...), or None."""
    # rsplit is os.path.basename exactly; Path.name would also drop a trailing `/` or `/.`.
    if not seg or seg[0].rsplit("/", 1)[-1] not in SHELLS:
        return None
    for i, a in enumerate(seg[1:-1], 1):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a):
            return seg[i + 1]
    return None


def expand_home(path: str) -> str:
    """Expand a leading `~` or `~user` like the shell; leave it unchanged when unknown."""
    return os.path.expanduser(path)  # noqa: PTH111 -- Path.expanduser raises on unknown ~user


def resolve(base: str, path: str) -> str:
    """Return `path` (with `~` expanded) resolved against `base`, normalised lexically."""
    # Path has no lexical normpath: resolving `..` must not follow symlinks or touch disk.
    return os.path.normpath(os.path.join(base, expand_home(path)))  # noqa: PTH118 -- see above


def path_exists(path: str) -> bool:
    """Whether `path` exists; False on any OSError/ValueError, like os.path.exists."""
    try:
        _ = Path(path).stat()
    except (OSError, ValueError):
        return False
    return True


def clean_env() -> dict[str, str]:
    """Environment without GIT_* vars, which git sets while running its own hooks."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run_git(cwd: str, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run `git -C <cwd> <args>`; None when git cannot be run or times out."""
    try:
        return subprocess.run(  # noqa: S603 -- fixed git argv, no shell
            ["git", "-C", cwd, *args],  # noqa: S607 -- git is looked up on PATH on purpose
            capture_output=True,
            text=True,
            timeout=5,
            env=clean_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def current_branch(cwd: str) -> str:
    """Return the branch checked out in `cwd`, or "" (detached, not a repo, git error)."""
    r = run_git(cwd, "branch", "--show-current")
    return r.stdout.strip() if r else ""


def parse_git(args: list[str], cwd: str) -> tuple[str, str, list[str], list[str]]:
    """Return (directory, subcommand, subcommand args, `-c` values) for `git <args>`."""
    configs: list[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        if opt == "-C" and i + 1 < len(args):
            cwd = resolve(cwd, args[i + 1])
            i += 2
        elif opt == "-c" and i + 1 < len(args):
            configs.append(args[i + 1])
            i += 2
        elif opt in ("--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
        else:
            i += 1
    if i >= len(args):
        return cwd, "", [], configs
    return cwd, args[i], args[i + 1 :], configs


def alias_value(name: str, configs: list[str], cwd: str) -> str | None:
    """Return the value of git alias `name` (`-c` values first, then git config), or None."""
    key = f"alias.{name}".lower()
    for c in reversed(configs):
        k, _, v = c.partition("=")
        if k.lower() == key:
            return v
    r = run_git(cwd, "config", "--get", f"alias.{name}")
    return r.stdout.strip() if r and r.returncode == 0 else None


def expand_alias(
    sub: str, rest: list[str], configs: list[str], cwd: str
) -> tuple[str, list[str], str | None]:
    """Expand git aliases. Returns (subcommand, args, shell command of a `!` alias or None)."""
    for _ in range(5):  # aliases may chain; git itself stops on loops
        if not sub or sub in GIT_BUILTINS:
            break
        value = alias_value(sub, configs, cwd)
        if not value:
            break
        if value.startswith("!"):
            return sub, rest, " ".join([value[1:], *map(shlex.quote, rest)])
        try:
            words = shlex.split(value)
        except ValueError:
            break
        if not words:
            break
        sub, rest = words[0], words[1:] + rest
    return sub, rest, None


def pushes_to_protected(refspecs: list[str], cwd: str) -> bool:
    """Whether any refspec's destination is the protected branch."""
    for spec in refspecs:
        dst = spec.lstrip("+").split(":")[-1]
        if dst in ("HEAD", "@"):
            dst = current_branch(cwd)
        if dst in (PROTECTED, f"refs/heads/{PROTECTED}"):
            return True
    return False


def is_commit(cwd: str, rev: str) -> bool:
    """Whether `rev` names a commit in the repo at `cwd`."""
    r = run_git(cwd, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return r is not None and r.returncode == 0


def checkout_switches(rest: list[str], cwd: str) -> bool:
    """Whether `git checkout <rest>` switches branch rather than restoring files."""
    if any(a in SWITCH_OPTS or a.startswith(("--orphan=", "--track=")) for a in rest):
        return True
    if "--" in rest or any(a in ("-p", "--patch") for a in rest):
        return False
    positional = [a for a in rest if a == "-" or not a.startswith("-")]
    if len(positional) != 1:  # none: stays on the branch; two or more: <tree-ish> <paths>
        return False
    target = positional[0]
    if target == ".":
        return False
    return is_commit(cwd, target) or not path_exists(resolve(cwd, target))


def creates_commit(sub: str, rest: list[str]) -> bool:
    """Whether `git <sub> <rest>` creates a commit on the current branch."""
    if sub not in NO_COMMIT_OPTS:
        return False
    return not any(a.split("=", 1)[0] in NO_COMMIT_OPTS[sub] for a in rest)


def pr_checks(args: list[str], cwd: str) -> tuple[int, str]:
    """Run `gh pr checks <args>`; exit 0 means every check passed."""
    try:
        r = subprocess.run(  # noqa: S603 -- fixed gh argv, no shell
            ["gh", "pr", "checks", *args],  # noqa: S607 -- gh is looked up on PATH on purpose
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
            env=clean_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"could not run gh pr checks: {e}"
    return r.returncode, (r.stdout + r.stderr).strip()


def check_pr_merge(rest: list[str], cwd: str) -> str | None:
    """Return the reason to deny `gh pr merge <rest>` (CI not green), or None."""
    selector: list[str] = []
    repo: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in GH_VALUE_OPTS and i + 1 < len(rest):
            if a in ("-R", "--repo"):
                repo = [a, rest[i + 1]]
            i += 2
            continue
        if a.startswith("--repo="):
            repo = [a]
        elif not a.startswith("-"):
            selector = [a]
        i += 1
    code, output = pr_checks(selector + repo, cwd)
    if code == 0:
        return None
    return CHECKS_MSG.format(code=code, output=output[-1500:])


def sets_hooks_path(opts: list[str], sub: str, rest: list[str], flags: set[str]) -> bool:
    """Whether a git command (global `opts`, `sub`, `rest`) points core.hooksPath elsewhere."""
    return (
        "hookspath" in flags
        or any("hookspath" in a.lower() for a in opts)
        or (sub == "config" and any("hookspath" in a.lower() for a in rest))
    )


def skips_hooks(sub: str, rest: list[str], flags: set[str]) -> bool:
    """Whether `git <sub> <rest>` skips the hooks (SKIP=, --no-verify, `commit -n`)."""
    if sub in ("commit", "push", "merge") and ("skip" in flags or "--no-verify" in rest):
        return True
    return sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in rest)


def push_targets_protected(rest: list[str], cwd: str) -> bool:
    """Whether `git push <rest>` run in `cwd` pushes to the protected branch."""
    positional = [a for a in rest if not a.startswith("-")]
    refspecs = positional[1:]
    if pushes_to_protected(refspecs, cwd):
        return True
    return not refspecs and current_branch(cwd) == PROTECTED


def check_git_command(sub: str, rest: list[str], cwd: str, flags: set[str]) -> str | None:
    """Return the reason to deny `git <sub> <rest>` (aliases expanded), or None."""
    if skips_hooks(sub, rest, flags):
        return BYPASS_MSG
    if sub == "switch" or (sub == "checkout" and checkout_switches(rest, cwd)):
        return SWITCH_MSG
    if creates_commit(sub, rest) and current_branch(cwd) == PROTECTED:
        return COMMIT_MSG
    if sub == "push" and push_targets_protected(rest, cwd):
        return PUSH_MSG
    return None


def check_git(seg: list[str], cwd: str, flags: set[str]) -> str | None:
    """Return the reason to deny the git command `seg` run in `cwd`, or None."""
    git_cwd, sub, rest, configs = parse_git(seg[1:], cwd)
    opts = seg[1 : len(seg) - len(rest)]  # global options before the subcommand
    if sets_hooks_path(opts, sub, rest, flags):
        return BYPASS_MSG
    sub, rest, shell = expand_alias(sub, rest, configs, git_cwd)
    if shell is not None:  # `!` alias: git runs it from the top of the work tree
        return check(shell, git_cwd, flags)
    return check_git_command(sub, rest, git_cwd, flags)


def change_dir(seg: list[str], cwd: str, dirs: list[str]) -> str | None:
    """Apply `cd`/`pushd`/`popd` to `cwd` (and the pushd stack); None for other commands."""
    if seg[0] == "cd":
        return resolve(cwd, seg[1]) if len(seg) > 1 else expand_home("~")
    if seg[0] == "pushd":
        args = [a for a in seg[1:] if not a.startswith(("-", "+"))]
        if args:
            dirs.append(cwd)
            cwd = resolve(cwd, args[0])
        return cwd
    if seg[0] == "popd":
        return dirs.pop() if dirs else cwd
    return None


def check_simple(seg: list[str], cwd: str, flags: set[str]) -> str | None:
    """Return the reason to deny one unwrapped simple command run in `cwd`, or None."""
    script = shell_script(seg)
    if script is not None:
        return check(script, cwd, flags)
    if seg[0] == "gh" and seg[1:3] == ["pr", "merge"]:
        return check_pr_merge(seg[3:], cwd)
    if seg[0] == "gh" and len(seg) > 1 and seg[1] == "api":
        if any(re.search(r"/pulls/[^/\s]+/merge\b", a) for a in seg[2:]):
            return MERGE_API_MSG
        return None
    if seg[0] == "git":
        return check_git(seg, cwd, flags)
    return None


def check(
    command: str, cwd: str, inherited: set[str] | None = None, *, strict: bool = True
) -> str | None:
    """Return the reason to deny `command` run in `cwd`, or None to allow it.

    `inherited` holds env_flags set by the caller (`SKIP=x bash -c '...'`). With
    `strict`, an unparseable command that mentions git/gh is denied; heredoc bodies are
    checked without it, since they are usually text, not scripts.
    """
    inherited = inherited or set()
    try:
        segs, bodies = segments(command)
    except ValueError:  # unbalanced quotes
        return PARSE_MSG if strict and GIT_WORD.search(command) else None
    for body in bodies:  # `bash <<EOF` runs its body
        reason = check(body, cwd, inherited, strict=False)
        if reason:
            return reason
    stack: list[str] = []  # cwd outside each `( ... )`
    dirs: list[str] = []  # pushd stack
    for raw in segs:
        if raw == ["("]:
            stack.append(cwd)
            continue
        if raw == [")"]:
            cwd = stack.pop() if stack else cwd
            continue
        seg, flags, seg_cwd = unwrap(raw, cwd)
        flags |= inherited
        if not seg:
            continue
        new_cwd = change_dir(seg, cwd, dirs)
        if new_cwd is not None:
            cwd = new_cwd
            continue
        reason = check_simple(seg, seg_cwd, flags)
        if reason:
            return reason
    return None


def deny(reason: str) -> None:
    """Write the PreToolUse decision that denies the command, with `reason`."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def read_input() -> tuple[str, str | None]:
    """Read the hook input from stdin; return the command and the session cwd (if any)."""
    # The casts only name the expected shape: a wrong shape still raises, and main denies.
    data = cast("dict[str, dict[str, object]]", json.load(sys.stdin))
    command = data["tool_input"]["command"]
    if not isinstance(command, str):
        msg = f"command is {type(command).__name__}, not str"
        raise TypeError(msg)
    return command, cast("str | None", data.get("cwd"))


def main() -> None:
    """Run the hook: print a deny decision for a risky command, nothing otherwise."""
    # Fail closed: a guard that crashes would otherwise let the command run.
    try:
        command, cwd = read_input()
        reason = check(command, cwd or str(Path.cwd()))
    except Exception as e:  # noqa: BLE001 -- fail closed: any error denies the command
        reason = ERROR_MSG.format(name=type(e).__name__, error=e)
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
