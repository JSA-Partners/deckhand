"""The finish step: the branch a human approved becomes a pull request, or nothing happens.

Reading the branch line by line is this step's first act, before anything here runs: the loop of
exports and fixes is what earns the sha the `Reviewed:` entry names, so no code reaches a pull
request unread. `context` prints what the branch changed, the pull request message it would open,
and the check commands the project's own files say it runs. The message is what the human approves:
the repository squashes, so the pull request title and body are the commit that lands on main and
the branch's own commits never do. The commits themselves are read by the gate that checks their
subjects rather than printed, because the review above has already put them in front of a person.

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
import contextlib
import io
import json
import re
import subprocess
from pathlib import Path

from deckhand import config, document, fields, gh, git, issue, log, naming, sections
from deckhand.config import Settings
from deckhand.step import MAIN, ORIGIN_MAIN, Refusal, block, indented, refuse_git, step, trunk

CONVENTIONAL = re.compile(rf"^({'|'.join(config.TYPES)})(\([^)]+\))?!?: .+$")
STORY_NUMBER = re.compile(r"#[0-9]+")
UNFINISHED = re.compile(r"\b(wip|fixup|squash)\b", re.IGNORECASE)
ATTRIBUTION = re.compile(r"^(co-authored-by|claude-session|signed-off-by)\s*:|generated with", re.IGNORECASE)
MAKE_TEST = re.compile(r"^test[ \t]*:", re.MULTILINE)
PYTEST_TABLE = "[tool.pytest"
TAIL = 10  # lines of a failed check's output, enough to name the failure without a wall of text
DIRTY = 5  # lines of a dirty tree, enough to recognise what is uncommitted


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
        raise Refusal(f"#{number} has no Story section; run /deckhand:next {number}")
    return naming.pr_body(number, text, breaking=breaking)


def _breaking(value: str | None) -> str | None:
    """The text of `--breaking`, or a refusal; an empty one would bang the title and say nothing."""
    if value is not None and not value.strip():
        raise Refusal("--breaking needs the text a client must react to")
    return value


def _message(settings: Settings, repo: str, number: int, breaking: str | None, story: issue.Issue) -> tuple[str, str]:
    """The title and body the pull request opens with, which the squash makes the commit message.

    `apply` computes it before any gate runs: a check suite can run for minutes, and a story off the
    board or without a Story section has nothing to say on main.
    """
    kind = fields.get_fields(settings, repo, number, ("Kind",))["Kind"]
    if kind is None:
        raise Refusal(f"#{number} has no Kind; run /deckhand:next {number}")
    return _title(settings, kind, story.title, bool(breaking)), _body(number, story, breaking)


# --- context ----------------------------------------------------------------


def _stat_block(base: str) -> list[str]:
    return indented(git.run("diff", "--stat", f"{base}...HEAD").splitlines())


def _pr_block(number: int) -> list[str]:
    """The message apply would open with, blank line and all, because that is what a human approves."""
    repo = gh.repo_slug()
    title, body = _message(config.load(), repo, number, None, issue.view(repo, number))
    return [f"  Title: {title}", *[f"  {line}".rstrip() for line in body.splitlines()]]


def context(args: argparse.Namespace) -> int:
    """Print the branch's diff stat, the pull request message, and the checks it would run.

    The branch review is this step's first act and runs before any of this is acted on: what is
    printed here is read against a branch a person has already been through comment by comment.
    """
    base = trunk()
    block("## Diff stat", lambda: _stat_block(base))
    block("## Pull request", lambda: _pr_block(args.issue))
    block("## Checks detected", lambda: indented(checks(Path.cwd()), "none detected"))
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


def _reviewed_head(story: issue.Issue, number: int) -> None:
    """Refuse unless HEAD is the commit the last `Reviewed:` entry names, or only docs sit past it.

    Nothing unread opens a pull request. The one exception is `docs/claude/`, which the document
    step writes after the person's pass and which is Claude's alone to keep, so commits past the
    reviewed one that touch nothing else are let through. Both shas go through rev-parse, and the
    named side as a commit, because a full sha that names nothing in this clone would otherwise
    come back as itself.
    """
    entry = log.last(story, "Reviewed:")
    if entry is None:
        raise Refusal(f"no Reviewed: entry on #{number}; review the branch first")
    named = entry.text.split()[0]
    head = refuse_git("rev-parse", "--verify", "HEAD")
    try:
        wanted = git.run("rev-parse", "--verify", f"{named}^{{commit}}")
    except git.GitError as error:
        raise Refusal(f"the last Reviewed: entry names {named}, which this clone does not have") from error
    if head == wanted:
        return
    try:
        git.run("merge-base", "--is-ancestor", wanted, head)
    except git.GitError as error:
        raise Refusal(f"HEAD {head} is not the last reviewed commit {wanted}; review the branch again") from error
    changed = git.run("diff", "--name-only", f"{wanted}..{head}").splitlines()
    unread = [path for path in changed if not path.startswith(f"{document.DEFAULT_DIR}/")]
    if unread:
        raise Refusal(f"HEAD {head} is not the last reviewed commit {wanted}; review the branch again")


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


def _attribution(message: str) -> str | None:
    """The first attribution trailer in a message, or None; whose hands wrote it is not the record.

    A trailer is a claim about authorship that outlives the session that added it, and a harness
    that asks for one is asking for a footer this project does not keep.
    """
    for line in message.splitlines():
        said = line.strip()
        if ATTRIBUTION.search(said):
            return f"attribution trailer: {said}"
    return None


def _messages() -> list[tuple[str, str]]:
    """Every commit on the branch as its short sha and its full message, oldest first.

    One `git log` reads them: the records are separated by a record separator and the sha from the
    message by a null, neither of which a commit message can carry.
    """
    log = refuse_git("log", "--reverse", "--format=%h%x00%B%x1e", f"{ORIGIN_MAIN}..HEAD")
    found = []
    for record in log.split("\x1e"):
        short, _, message = record.strip().partition("\x00")
        if short:
            found.append((short, message))
    return found


def _conventional_commits() -> None:
    """Refuse on the first commit of the branch whose message is not one a reader can read.

    The subject carries the reading, and the whole message carries the rule against trailers. A
    squash means none of these commits reaches main, but no trailer belongs anywhere: the branch is
    read as it stands, and a footer a session was told to add is still a footer.
    """
    for short, message in _messages():
        subject = message.partition("\n")[0]
        fault = _fault(subject) or _attribution(message)
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


def _configure(parser: argparse.ArgumentParser) -> None:
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
    title, body = _message(settings, repo, args.issue, breaking, story)
    branch = _branch()
    _reviewed_head(story, args.issue)
    _clean_tree()
    _contains_main()
    _conventional_commits()
    _docs_audit()
    for command in args.check:
        _check(command)

    # A plain push, with -u for the first time; git's own non-fast-forward refusal stands where the
    # lease once did. Past the refusal point: a failure here reaches cli.main.
    git.run("push", "-u", "origin", branch)
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
