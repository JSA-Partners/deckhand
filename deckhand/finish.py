"""The finish step: the branch a human approved becomes a pull request, or nothing happens.

Reading the branch line by line is this step's first act, before anything here runs: the loop of
exports and fixes is what earns the sha the `Reviewed:` entry names, so no code reaches a pull
request unread. `context` prints the branch's commits, what it changed, the pull request message it
would open, and the check commands the project's own files say it runs. The commits are the one
thing the Reviewed: entry is checked against, and no other block in this context answers what they
are. The message is what the human approves: the repository squashes, so the pull request title and
body are the commit that lands on main and the branch's own commits never do.

`apply` is the one gate of the loop, and every part of it is a refusal before a single write. The
pull request opens only when HEAD is the commit the last `Reviewed:` entry names, the tree is
clean, the branch contains main, every subject is a conventional commit that names no story
number, the docs audit is clean, and every check the caller named exits 0. Only then does it push
the branch, which reaches origin here and nowhere earlier, open the pull request assigned to
whoever ran the step, log `Pull request:` on the issue, and record Pending Review and Actual, so
what merges is what was reviewed.

Both verbs work against `origin/main`, never the local branch of that name: a story branches from
origin/main and local main is routinely behind it, so a range against local main would hand the
gates a colleague's commits to answer for. `context` falls back to local main, because printing a
branch's own diff is worth doing without a remote; `apply` fetches first and does not.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from deckhand import config, fields, gates, gh, git, issue, log, naming
from deckhand.config import Settings
from deckhand.step import (
    MAIN,
    TAIL,
    Refusal,
    block,
    draft_line,
    indented,
    read_draft,
    ref_label,
    refuse_git,
    step,
    trunk,
)

MAKE_TEST = re.compile(r"^test[ \t]*:", re.MULTILINE)
PYTEST_TABLE = "[tool.pytest"


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


# `i.e.` sits a word boundary away from a standalone I, and the style guide allows it sparingly.
_FIRST_PERSON = re.compile(r"\bI\b(?!\.)|\b(?:we|our|ours|my)\b", re.IGNORECASE)
# `_pr_block` shows the footer this appends, and a summary that already carries one duplicates it:
# `naming.pr_body` appends its own unconditionally, so what the session wrote lands twice.
_CLOSING = re.compile(r"^(?:Closes|Fixes|Resolves)\s+#\d+\s*$", re.IGNORECASE | re.MULTILINE)
PREVIEW = "<the summary you write goes here>"


def _body(number: int, story: issue.Issue, breaking: str | None, summary: str) -> str:
    """The summary the session wrote, its deviations, and the footers; the Story is not read.

    A Story is written "As a ..., I want ...", so copying it put first person into a shared artifact
    and, on the squash, into main's history for good. What a branch changed cannot be computed from
    the issue, so the session writes it and this refuses what it must not carry.
    """
    text = summary.strip()
    if not text:
        raise Refusal(f"#{number} needs a summary: one or two paragraphs of what the branch changed")
    found = _FIRST_PERSON.search(text)
    if found is not None:
        raise Refusal(f"the summary says {found.group(0)!r}; a pull request body is written in the third person")
    closing = _CLOSING.search(text)
    if closing is not None:
        offending = closing.group(0).strip()
        raise Refusal(f"the summary already ends with {offending!r}; finish appends its own Closes #{number} footer")
    deviations = [entry.body.partition("Deviation:")[2] for entry in log.entries(story) if entry.prefix == "Deviation:"]
    return naming.pr_body(number, text, breaking=breaking, deviations=deviations)


def _push(branch: str) -> None:
    """Push the branch; a refusal carries the tail of what git said, which is where a hook names its reason."""
    try:
        git.run("push", "-u", "origin", branch)
    except git.GitError as error:
        lines = [line for line in error.stderr.splitlines() if line.strip()]
        tail = [line for line in lines[-TAIL:] if line.strip() != str(error)]
        raise Refusal("\n".join([str(error), *tail])) from error


def _breaking(value: str | None) -> str | None:
    """The text of `--breaking`, or a refusal; an empty one would bang the title and say nothing."""
    if value is not None and not value.strip():
        raise Refusal("--breaking needs the text a client must react to")
    return value


def _message(
    settings: Settings, repo: str, number: int, breaking: str | None, story: issue.Issue, summary: str
) -> tuple[str, str]:
    """The title and body the pull request opens with, which the squash makes the commit message.

    `apply` computes it before any gate runs: a check suite can run for minutes, and a story off the
    board or without a Story section has nothing to say on main.
    """
    kind = fields.get_fields(settings, repo, number, ("Kind",))["Kind"]
    if kind is None:
        raise Refusal(f"#{number} has no Kind; run /deckhand:next {number}")
    return _title(settings, kind, story.title, bool(breaking)), _body(number, story, breaking, summary)


# --- context ----------------------------------------------------------------


def _commits_block(base: str) -> list[str]:
    """The commits this branch adds, which is what the Reviewed: entry is checked against."""
    return indented(refuse_git("log", "--oneline", f"{base}..HEAD").splitlines(), "none")


def _stat_block(base: str) -> list[str]:
    return indented(git.run("diff", "--stat", f"{base}...HEAD").splitlines())


def _pr_block(number: int) -> list[str]:
    """The message apply would open with, blank line and all, because that is what a human approves."""
    repo = gh.repo_slug()
    title, body = _message(config.load(), repo, number, None, issue.view(repo, number), PREVIEW)
    lines = [f"  Title: {title}", *[f"  {line}".rstrip() for line in body.splitlines()]]
    lines.append("  (the footer above is generated by finish; do not add it to the summary)")
    return lines


def context(args: argparse.Namespace) -> int:
    """Print the branch's diff stat, the pull request message, and the checks it would run.

    The branch review is this step's first act and runs before any of this is acted on: what is
    printed here is read against a branch a person has already been through comment by comment.
    """
    base = trunk()
    block("## Commits", lambda: _commits_block(base))
    block("## Diff stat", lambda: _stat_block(base))
    block("## Pull request", lambda: _pr_block(args.issue))
    block("## Checks detected", lambda: indented(checks(Path.cwd()), "none detected"))
    print(draft_line("Summary", f"{args.issue}-summary.md"))
    return 0


# --- apply ------------------------------------------------------------------


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


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, help="the summary of what the branch changed")
    parser.add_argument("--actual", required=True, help="the points the story actually took")
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
    """Gate a story branch, then push it, open its pull request, and log the pull request."""
    actual = _actual(args.actual)
    breaking = _breaking(args.breaking)
    settings = config.load()
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    open_blockers = issue.blockers(repo, args.issue)
    if open_blockers:
        named = "; ".join(f"{ref_label(where, number, repo)} {title}" for where, number, title in open_blockers)
        raise Refusal(f"blocked by {named}; the pull request opens when it closes")
    title, body = _message(settings, repo, args.issue, breaking, story, read_draft(args.file))
    branch = _branch()
    gates.reviewed_head(story, args.issue)
    gates.clean_tree()
    gates.contains_main()
    gates.conventional_commits()
    gates.docs_audit()
    for command in args.check:
        gates.check(command)

    # A plain push, with -u for the first time; git's own non-fast-forward refusal stands where the
    # lease once did.
    _push(branch)
    print("Pushed")
    url = issue.pull_request(repo, branch)
    if url:
        print(f"Reusing {url}")
        # The run that opened it may have failed before the fields were set, and a re-run is often a
        # colleague's, so the reused pull request is assigned the same way a new one is.
        gh.run("pr", "edit", url, "--repo", repo, "--add-assignee", "@me")
        print("Assigned @me")
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
            # Whoever runs the step answers for the branch, so the pull request carries their name
            # rather than arriving unowned in a review queue.
            "--assignee",
            "@me",
        ).strip()
        print(f"Opened {url}")
    issue.comment(repo, args.issue, log.checked(f"Pull request: {url}"))
    print("Logged Pull request")
    print(fields.set_field(settings, repo, args.issue, "Status", "Pending Review"))
    print(fields.set_field(settings, repo, args.issue, "Actual", str(actual)))
    return 0
