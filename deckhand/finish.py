"""The finish step: the branch a human approved becomes a pull request, or nothing happens.

`context` prints the commits on the branch, what they touched, the pull request message it would
open, the check commands the project's own files say it runs, and the branch review command the
human runs before any of this. The message is what the human approves: the repository squashes, so
the pull request title and body are the commit that lands on main and the branch's own commits
never do.

`apply` is the one gate of the loop, and every part of it is a refusal before a single write. The
pull request opens only when HEAD is still the commit the human approved, the tree is clean, the
branch contains main, every subject is a conventional commit that names no story number, the docs
audit is clean, and every check the caller named exits 0. Only then does it push, open the pull
request, and record Pending Review and Actual, so what merges is what was approved.

Both verbs work against `origin/main`, never the local branch of that name: a story branches from
origin/main and local main is routinely behind it, so a range against local main would hand the
gates a colleague's commits to answer for. `context` falls back to local main, because printing a
branch's own commits is worth doing without a remote; `apply` fetches first and does not.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
from pathlib import Path

from deckhand import config, document, fields, gh, git, issue, naming, sections
from deckhand.config import Settings
from deckhand.step import MAIN, ORIGIN_MAIN, Refusal, block, indented, refuse_git, step, trunk

CONVENTIONAL = re.compile(rf"^({'|'.join(config.TYPES)})(\([^)]+\))?!?: .+$")
STORY_NUMBER = re.compile(r"#[0-9]+")
UNFINISHED = re.compile(r"\b(wip|fixup|squash)\b", re.IGNORECASE)
MAKE_TEST = re.compile(r"^test[ \t]*:", re.MULTILINE)
PYTEST_TABLE = "[tool.pytest"
TAIL = 10  # lines of a failed check's output, enough to name the failure without a wall of text
DIRTY = 5  # lines of a dirty tree, enough to recognise what is uncommitted
NEXT = "Next: merge. Merging closes the issue, and the project's built-in automation sets Done."


def _text(path: Path) -> str:
    """The file's text, or empty when it is not there or cannot be read; detection never fails."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- the checks the project looks like it runs -------------------------------


def _npm_checks(cwd: Path) -> list[str]:
    """`npm run test` and `npm run lint`, for whichever of the two package.json defines."""
    try:
        data = json.loads(_text(cwd / "package.json"))
    except ValueError:
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    return [f"npm run {name}" for name in ("test", "lint") if name in scripts]


def checks(cwd: Path) -> list[str]:
    """The check commands the project's own files say it runs, in the order they should be run."""
    found = []
    if (cwd / ".pre-commit-config.yaml").is_file():
        uv = "uv run " if (cwd / "uv.lock").is_file() else ""
        found.append(f"{uv}pre-commit run --all-files")
    if PYTEST_TABLE in _text(cwd / "pyproject.toml"):
        found.append("uv run pytest -q")
    found += _npm_checks(cwd)
    if MAKE_TEST.search(_text(cwd / "Makefile")):
        found.append("make test")
    return found


# --- the pull request message -----------------------------------------------


def _title(settings: Settings, kind: str, story_title: str, breaking: bool) -> str:
    try:
        return naming.pr_title(settings, kind, story_title, breaking=breaking)
    except ValueError as error:
        raise Refusal(str(error)) from error


def _body(number: int, story: issue.Issue, breaking: str | None) -> str:
    """The story as the commit body; a story that says nothing has no message to squash into main."""
    text = sections.get(story.body, "Story", "").strip()
    if not text:
        raise Refusal(f"#{number} has no Story section; run /deckhand:amend {number}")
    return naming.pr_body(number, text, breaking=breaking)


def _breaking(value: str | None) -> str | None:
    """The text of `--breaking`, or a refusal; an empty one would bang the title and say nothing."""
    if value is not None and not value.strip():
        raise Refusal("--breaking needs the text a client must react to")
    return value


def _message(settings: Settings, repo: str, number: int, breaking: str | None) -> tuple[str, str]:
    """The title and body the pull request opens with, which the squash makes the commit message.

    `apply` computes it before any gate runs: a check suite can run for minutes, and a story off the
    board or without a Story section has nothing to say on main.
    """
    kind = fields.get_fields(settings, repo, number, ("Kind",))["Kind"]
    if kind is None:
        raise Refusal(f"#{number} has no Kind; run /deckhand:ready {number}")
    story = issue.view(repo, number)
    return _title(settings, kind, story.title, bool(breaking)), _body(number, story, breaking)


# --- context ----------------------------------------------------------------


def _commits_block(base: str) -> list[str]:
    """Every commit on the branch with the files it touched, shown for the person's review.

    Nothing here rewrites them: what the branch holds is what the gates answer for, and the squash
    message is the one thing the person approves.
    """
    return indented(git.run("log", "--reverse", "--name-only", f"{base}..HEAD", "--format=%h %s").splitlines())


def _stat_block(base: str) -> list[str]:
    return indented(git.run("diff", "--stat", f"{base}...HEAD").splitlines())


def _pr_block(number: int) -> list[str]:
    """The message apply would open with, blank line and all, because that is what a human approves."""
    title, body = _message(config.load(), gh.repo_slug(), number, None)
    return [f"  Title: {title}", *[f"  {line}".rstrip() for line in body.splitlines()]]


def context(args: argparse.Namespace) -> int:
    """Print the branch's commits and diff, the pull request message, the checks, and the review command."""
    base = trunk()
    block("## Commits", lambda: _commits_block(base))
    block("## Diff stat", lambda: _stat_block(base))
    block("## Pull request", lambda: _pr_block(args.issue))
    block("## Checks detected", lambda: indented(checks(Path.cwd()), "none detected"))
    print(f"Branch review: tuicr -r {base}..HEAD --stdout")
    return 0


# --- the gates --------------------------------------------------------------


def _actual(value: str) -> int:
    if not (value.isascii() and value.isdigit()):
        raise Refusal(f"actual must be a non-negative integer, got {value!r}")
    return int(value)


def _branch() -> str:
    """The branch being finished; the pull request needs a head, so a detached HEAD is a refusal."""
    name = refuse_git("branch", "--show-current")
    if not name:
        raise Refusal("HEAD is detached; check out the story branch")
    return name


def _approved_head(approved: str) -> None:
    """Refuse unless HEAD is still the commit the human approved; both sides go through rev-parse."""
    head = refuse_git("rev-parse", "--verify", "HEAD")
    try:
        wanted = git.run("rev-parse", "--verify", approved)
    except git.GitError as error:
        raise Refusal(f"cannot resolve --approved {approved}: {error}") from error
    if head != wanted:
        raise Refusal(f"HEAD moved since approval: expected {wanted}, got {head}")


def _clean_tree() -> None:
    status = refuse_git("status", "--porcelain")
    if status.strip():
        raise Refusal("\n".join(["the working tree is not clean", *indented(status.splitlines()[:DIRTY])]))


def _contains_main() -> None:
    """Fetch the trunk, then refuse a branch that does not contain it; every later range needs it."""
    refuse_git("fetch", "origin", MAIN)
    try:
        git.run("merge-base", "--is-ancestor", ORIGIN_MAIN, "HEAD")
    except git.GitError as error:
        raise Refusal("branch does not contain main; rebase first") from error


def _fault(subject: str) -> str | None:
    """Why this subject cannot ship, or None; the pull request is where a story number belongs."""
    if not CONVENTIONAL.match(subject):
        return f"not a conventional commit subject: {subject}"
    if STORY_NUMBER.search(subject):
        return f"a story number in the subject: {subject}"
    if UNFINISHED.search(subject):
        return f"work in progress: {subject}"
    return None


def _conventional_commits() -> None:
    """Refuse on the first commit of the branch whose subject is not one a reader can read."""
    log = refuse_git("log", "--format=%h %s", f"{ORIGIN_MAIN}..HEAD")
    for line in reversed(log.splitlines()):  # oldest first, so the first offender is the first one
        short, _, subject = line.partition(" ")
        fault = _fault(subject)
        if fault is not None:
            raise Refusal(f"commit {short}: {fault}")


def _docs_audit() -> None:
    """Refuse when the docs audit lists anything: a finding is always indented under its file."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        document._audit(document.DEFAULT_DIR)  # it always returns 0; what it printed is the answer
    said = output.getvalue().splitlines()
    if not any(line.startswith("  ") for line in said):
        return
    raise Refusal("\n".join(["docs audit found stale files; run /deckhand:document audit", *indented(said)]))


def _check(command: str) -> None:
    """Run one check the caller named; a non-zero exit is a refusal carrying what it said.

    The two streams are merged, so the tail is the end of the run as the terminal would have shown
    it rather than one stream's end with the other's cause missing.
    """
    print(f"Running {command}", flush=True)
    result = subprocess.run(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    if result.returncode == 0:
        return
    said = [line for line in result.stdout.splitlines() if line.strip()]
    raise Refusal("\n".join([f"check failed: {command}", *indented(said[-TAIL:], "no output")]))


# --- apply ------------------------------------------------------------------


def _existing_pr(repo: str, branch: str) -> str | None:
    """The pull request the branch already has, so a re-run finishes what a failed one started."""
    try:
        data = gh.json_out("pr", "view", branch, "--repo", repo, "--json", "url")
    except gh.GhError:
        return None
    return (data.get("url") or None) if isinstance(data, dict) else None


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actual", required=True, help="the points the story actually took")
    parser.add_argument("--approved", required=True, metavar="SHA", help="the HEAD the user approved")
    parser.add_argument(
        "--check",
        required=True,
        action="append",
        metavar="CMD",
        help="a command that must exit 0 before the pull request opens; repeat it for more than one",
    )
    parser.add_argument("--breaking", metavar="TEXT", help="what a client of this change must react to")


@step("finish", _configure)
def apply(args: argparse.Namespace) -> int:
    """Gate a story branch, then push it and open its pull request."""
    actual = _actual(args.actual)
    breaking = _breaking(args.breaking)
    settings = config.load()
    repo = gh.repo_slug()
    title, body = _message(settings, repo, args.issue, breaking)
    branch = _branch()
    _approved_head(args.approved)
    _clean_tree()
    _contains_main()
    _conventional_commits()
    _docs_audit()
    for command in args.check:
        _check(command)

    # Named remote and refspec, so a branch that tracks nothing still goes where it belongs, and the
    # lease refuses a colleague's commit even when an ambient fetch has already moved the tracking
    # ref. Past the refusal point: a failure here reaches cli.main.
    git.run("push", "--force-with-lease", "--force-if-includes", "origin", branch)
    print("Pushed")
    url = _existing_pr(repo, branch)
    if url:
        print(f"Reusing {url}")
    else:
        url = gh.run(
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            MAIN,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ).strip()
        print(f"Opened {url}")
    print(fields.set_field(settings, repo, args.issue, "Status", "Pending Review"))
    print(fields.set_field(settings, repo, args.issue, "Actual", str(actual)))
    print(NEXT)
    return 0
