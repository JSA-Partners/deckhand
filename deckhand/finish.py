"""The finish step: the branch a human approved becomes a pull request, or nothing happens.

Reading the branch line by line is this step's first act, before anything here runs: the loop of
exports and fixes is what earns the commit the reviewed ref names, so no code reaches a pull
request unread. `context` prints the branch's commits, what it changed, the pull request message it
would open, and the check commands the project's own files say it runs. The commits are the one
thing the reviewed ref is checked against, and no other block in this context answers what they
are. The message is what the human approves: the repository squashes, so the pull request title and
body are the commit that lands on main and the branch's own commits never do.

`apply` is the one gate of the loop, and every part of it is a refusal before a single write. The
pull request opens only when HEAD is the commit the reviewed ref names, the tree is
clean, the branch contains main, every subject is a conventional commit that names no story
number, the docs audit is clean, and every check the caller named exits 0. Only then does it push
the branch, which reaches origin here and nowhere earlier, open the pull request assigned to
whoever ran the step, log `Pull request:` on the issue, and record In Review, so
what merges is what was reviewed.

Both verbs work against `origin/main`, never the local branch of that name: a story branches from
origin/main and local main is routinely behind it, so a range against local main would hand the
gates a colleague's commits to answer for. `context` falls back to local main, because printing a
branch's own diff is worth doing without a remote; `apply` fetches first and does not.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from deckhand import columns, config, fields, gates, gh, git, invoke, issue, log, naming, sections
from deckhand.config import Settings
from deckhand.step import (
    MAIN,
    TAIL,
    Refusal,
    block,
    file_line,
    indented,
    read_file,
    ref_label,
    refuse_git,
    spill,
    step,
    trunk,
    usable,
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


def hooked(cwd: Path) -> set[str]:
    """The lines of the repository's pre-push hook, so a check it already runs is left to the push."""
    try:
        path = cwd / git.run("rev-parse", "--git-path", "hooks/pre-push", cwd=cwd)
    except git.GitError:
        return set()
    # git skips a hook it cannot execute, so such a hook runs nothing.
    if not os.access(path, os.X_OK):
        return set()
    return {line.strip() for line in _text(path).splitlines() if line.strip()}


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
SUMMARY_WORDS = 120
SUMMARY_RULE = (
    "One paragraph on what changed for the person using it and why; a second only for what a later "
    f"reader of main needs and the diff cannot show. Under {SUMMARY_WORDS} words, plain prose, no tables, "
    "headings or lists."
)
# The body is the commit message on main, where markdown structure is noise; a reference such as
# `#344` opening a wrapped line is prose, so a heading needs its hashes followed by a space.
_SHAPES = (
    ("table row", re.compile(r"^\s*\|")),
    ("heading", re.compile(r"^\s*#{1,6}\s")),
    ("list item", re.compile(r"^\s*(?:[-*+]|\d+\.)\s")),
)


def summary_problem(text: str) -> str | None:
    """The first rule a summary breaks, or None when it is short, plain prose."""
    words = len(text.split())
    if words > SUMMARY_WORDS:
        return f"the summary is {words} words; keep it under {SUMMARY_WORDS}"
    paragraphs = [chunk for chunk in re.split(r"\n\s*\n", text.strip()) if chunk.strip()]
    if len(paragraphs) > 2:
        return f"the summary has {len(paragraphs)} paragraphs; use one, two at most"
    for number, line in enumerate(text.strip().splitlines(), start=1):
        for name, shape in _SHAPES:
            if shape.match(line):
                return f"the summary is prose for a commit message; line {number} is a {name}"
    return None


def _body(number: int, breaking: str | None, summary: str) -> str:
    """The summary the session wrote and the footers; the Story is not read.

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
    problem = summary_problem(text)
    if problem is not None:
        raise Refusal(problem)
    closing = _CLOSING.search(text)
    if closing is not None:
        offending = closing.group(0).strip()
        raise Refusal(f"the summary already ends with {offending!r}; finish appends its own Closes #{number} footer")
    return naming.pr_body(number, text, breaking=breaking)


def _deviations(story: issue.Issue) -> list[str]:
    """What the branch did that the story did not say, one text per `Deviation:` entry in its log."""
    return [entry.body.partition("Deviation:")[2] for entry in log.entries(story) if entry.prefix == "Deviation:"]


def _push(branch: str) -> None:
    """Push the branch; a refusal carries the tail of both streams, which is where a hook names its reason."""
    try:
        git.run("push", "-u", "origin", branch)
    except git.GitError as error:
        said = error.stdout.splitlines() + error.stderr.splitlines()
        lines = [line for line in said if line.strip()]
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
    return _title(settings, kind, story.title, bool(breaking)), _body(number, breaking, summary)


# --- context ----------------------------------------------------------------


def _commits_block(base: str) -> list[str]:
    """The commits this branch adds, which is what the reviewed ref is checked against."""
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
    """Print the apply, the summary path and the review's brief, then the commits, diff stat, message, and checks.

    The branch review is this step's first act and runs before any of this is acted on, briefed with
    the story, its plan out of the fold, and the deviations the build logged.
    """
    base = trunk()
    detected = checks(Path.cwd())
    flags = [f'--check "{command}"' for command in detected] or ['--check "<cmd>"']
    print(invoke.apply_line("finish", str(args.issue), "<summary>", *flags))
    print(file_line("Summary", f"{args.issue}-summary.md"))
    story: issue.Issue | Exception
    try:
        story = issue.view(gh.repo_slug(), args.issue)
    except Exception as error:  # the commits and the checks are still worth printing without the story
        story = error
    print(spill("Body", f"{args.issue}-issue.md", lambda: sections.bare(usable(story).body)))
    print()
    block("## Deviations", lambda: indented(" ".join(text.split()) for text in _deviations(usable(story))))
    block("## Commits", lambda: _commits_block(base))
    block("## Diff stat", lambda: _stat_block(base))
    block("## Pull request", lambda: _pr_block(args.issue))
    block("## Summary rule", lambda: [f"  {SUMMARY_RULE}"])
    pushed = hooked(Path.cwd())
    named = [f"{command} (the pre-push hook runs it)" if command in pushed else command for command in detected]
    block("## Checks detected", lambda: indented(named, "none detected"))
    return 0


# --- apply ------------------------------------------------------------------


def _branch() -> str:
    """The branch being finished; the pull request needs a head, so a detached HEAD is a refusal."""
    name = refuse_git("branch", "--show-current")
    if not name:
        raise Refusal("HEAD is detached; check out the story branch")
    return name


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, help="the summary of what the branch changed")
    parser.add_argument(
        "--check",
        required=True,
        action="append",
        metavar="CMD",
        help="a command that must exit 0 before the pull request opens; repeat it for more than one",
    )
    parser.add_argument("--breaking", metavar="TEXT", help="what a client of this change must react to")


def _link(repo: str, number: int, url: str, body: str) -> None:
    """Re-save the body once when GitHub missed its `Closes` footer; the pull request stands either way."""
    if issue.pull_request_for(repo, number) is not None:
        return
    issue.resave_pull_request(repo, url, body)
    print(f"Relinked #{number}")
    if issue.pull_request_for(repo, number) is None:
        print(f"GitHub has not linked {url} to #{number}; re-save its body on GitHub if it stays unlinked")


@step("finish", _configure)
def apply(args: argparse.Namespace) -> int:
    """Gate a story branch, then push it, open its pull request, and log the pull request."""
    breaking = _breaking(args.breaking)
    settings = config.load()
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    open_blockers = issue.blockers(repo, args.issue)
    if open_blockers:
        named = "; ".join(f"{ref_label(where, number, repo)} {title}" for where, number, title in open_blockers)
        raise Refusal(f"blocked by {named}; the pull request opens when it closes")
    title, body = _message(settings, repo, args.issue, breaking, story, read_file(args.file))
    branch = _branch()
    gates.reviewed_head(args.issue)
    gates.clean_tree()
    gates.contains_main(args.issue)
    gates.conventional_commits()
    gates.docs_audit()
    pushed = hooked(Path.cwd())
    for command in args.check:
        if command in pushed:
            print(f"Left to the pre-push hook: {command}")
        else:
            gates.check(command)

    # A plain push, with -u for the first time; git's own non-fast-forward refusal stands where the
    # lease once did.
    _push(branch)
    print("Pushed")
    found = issue.pull_request(repo, branch)
    url = found[0] if found and found[1] == "OPEN" else None
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
    # Whoever runs the step answers for the branch. Assigned apart from the create, so a failed
    # assignment never costs the pull request, and a re-run that reuses one assigns it the same way.
    gh.run("pr", "edit", url, "--repo", repo, "--add-assignee", "@me")
    print("Assigned @me")
    _link(repo, args.issue, url, body)
    issue.comment(repo, args.issue, log.checked(f"Pull request: {url}"))
    print("Logged Pull request")
    print(fields.set_field(settings, repo, args.issue, "Status", columns.IN_REVIEW))
    return 0
