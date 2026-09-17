"""What must be true before a branch becomes a pull request.

Every gate raises `Refusal` and writes nothing. They are separate from the step that runs them so
that adding a gate does not grow the module that opens the pull request, and so the order they run
in is readable in one place: `finish.apply`.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess

from deckhand import config, document, git, issue, log
from deckhand.step import MAIN, ORIGIN_MAIN, TAIL, Refusal, indented, refuse_git

CONVENTIONAL = re.compile(rf"^({'|'.join(config.TYPES)})(\([^)]+\))?!?: .+$")
STORY_NUMBER = re.compile(r"#[0-9]+")
UNFINISHED = re.compile(r"\b(wip|fixup|squash)\b", re.IGNORECASE)
ATTRIBUTION = re.compile(r"^(co-authored-by|claude-session|signed-off-by)\s*:|generated with", re.IGNORECASE)
DIRTY = 5  # lines of a dirty tree, enough to recognise what is uncommitted


def reviewed_head(story: issue.Issue, number: int) -> None:
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
    if _merge_of_main(head, wanted) or _only_docs(wanted, head):
        return
    raise Refusal(f"HEAD {head} is not the last reviewed commit {wanted}; review the branch again")


def _only_docs(wanted: str, head: str) -> bool:
    """True when every file changed between the reviewed commit and `head` is under docs/claude."""
    changed = git.run("diff", "--name-only", f"{wanted}..{head}").splitlines()
    return all(path.startswith(f"{document.DEFAULT_DIR}/") for path in changed)


def _merge_of_main(head: str, wanted: str) -> bool:
    """True when `head` is a merge whose sides are the reviewed commit, or docs past it, and a commit of main.

    GitHub makes this commit when it brings a branch up to date; the only new lines are main's own,
    which were read when they merged. The reviewed side may carry docs commits, as it may without
    the merge, so it is checked the way the plain case is.
    """
    parents = git.run("rev-list", "--parents", "-n", "1", head).split()[1:]
    if len(parents) != 2:
        return False
    for ours, theirs in ((parents[0], parents[1]), (parents[1], parents[0])):
        try:
            git.run("merge-base", "--is-ancestor", wanted, ours)
            git.run("merge-base", "--is-ancestor", theirs, ORIGIN_MAIN)
        except git.GitError:
            continue
        if _only_docs(wanted, ours):
            return True
    return False


def clean_tree() -> None:
    status = refuse_git("status", "--porcelain")
    if status.strip():
        raise Refusal("\n".join(["the working tree is not clean", *indented(status.splitlines()[:DIRTY])]))


def contains_main() -> None:
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
    # A merge of main carries git's own subject and never reaches main through the squash.
    log = refuse_git("log", "--reverse", "--no-merges", "--format=%h%x00%B%x1e", f"{ORIGIN_MAIN}..HEAD")
    found = []
    for record in log.split("\x1e"):
        short, _, message = record.strip().partition("\x00")
        if short:
            found.append((short, message))
    return found


def conventional_commits() -> None:
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


def docs_audit() -> None:
    """Refuse when the docs audit lists anything: a finding is always indented under its file."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        document._audit(document.DEFAULT_DIR)  # it always returns 0; what it printed is the answer
    said = output.getvalue().splitlines()
    if not any(line.startswith("  ") for line in said):
        return
    raise Refusal("\n".join(["docs audit found stale files; run /deckhand:document audit", *indented(said)]))


def check(command: str) -> None:
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
