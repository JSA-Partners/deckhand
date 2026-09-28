"""The gates a branch must pass before `finish` opens its pull request."""

from __future__ import annotations

import itertools
import json
import os
import subprocess
from pathlib import Path

import pytest

from deckhand import gates, reviewed
from deckhand.step import TAIL, Refusal
from tests.conftest import FIXTURES


def test_check_returns_when_the_command_exits_zero():
    assert gates.check("true") is None


def test_check_refuses_a_failing_command_and_names_it():
    with pytest.raises(Refusal) as refused:
        gates.check("false")
    assert str(refused.value).splitlines()[0] == "check failed: false"


def test_check_truncates_a_long_failure_to_the_last_tail_lines():
    lines = [f"line{n}" for n in range(1, TAIL + 6)]
    command = "; ".join([*(f"echo {line}" for line in lines), "exit 1"])

    with pytest.raises(Refusal) as refused:
        gates.check(command)

    assert str(refused.value).splitlines()[1:] == [f"  {line}" for line in lines[-TAIL:]]


# --- _fault -------------------------------------------------------------


def test_fault_accepts_a_conventional_subject():
    assert gates._fault("fix(store): tidy the grant filter") is None


def test_fault_rejects_an_unconventional_subject():
    assert gates._fault("made the thing work") == "not a conventional commit subject: made the thing work"


def test_fault_rejects_a_story_number_in_the_subject():
    assert gates._fault("fix(store): close #248") == "a story number in the subject: fix(store): close #248"


def test_fault_rejects_a_wip_subject():
    subject = "fix(store): wip on the grant filter"
    assert gates._fault(subject) == f"work in progress: {subject}"


# --- _attribution ---------------------------------------------------------


def test_attribution_is_none_for_a_clean_message():
    assert gates._attribution("fix(store): tidy the grant filter\n") is None


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "Claude-Session: https://claude.ai/code/session_1",
        "Signed-off-by: Claude <noreply@anthropic.com>",
        "Generated with Claude Code",
    ],
)
def test_attribution_names_the_trailer_it_finds(trailer):
    message = f"fix(store): tidy the grant filter\n\n{trailer}\n"
    assert gates._attribution(message) == f"attribution trailer: {trailer}"


_when = itertools.count(1_700_000_000, 60)


def _commit(repo: Path, message: str) -> None:
    """Commit everything at a distinct time, so a later change is later to git's one-second clock."""
    stamp = f"{next(_when)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True, env=env)


# --- reviewed_head --------------------------------------------------------


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _sha(path: Path, ref: str) -> str:
    return _git(path, "rev-parse", ref).strip()


def _mark_reviewed(repo: Path, sha: str, number: int = 248) -> None:
    """Write the ref an apply of `reviewed` would write, without going through the step."""
    _git(repo, "update-ref", reviewed.ref_name(number), sha)


def test_reviewed_head_refuses_without_a_ref(repo):
    with pytest.raises(Refusal) as refused:
        gates.reviewed_head(248)

    assert str(refused.value) == "no review recorded for #248 in this clone; review the branch first"


def test_reviewed_head_accepts_head_at_the_reviewed_commit(repo):
    _mark_reviewed(repo, _sha(repo, "HEAD"))

    assert gates.reviewed_head(248) is None


def test_reviewed_head_refuses_a_descendant_that_touches_more_than_docs(repo):
    """The ref genuinely gates: moving HEAD past it on a non-docs change is refused."""
    wanted = _sha(repo, "HEAD")
    _mark_reviewed(repo, wanted)
    (repo / "f").write_text("changed\n")
    _commit(repo, "feat: change f")
    head = _sha(repo, "HEAD")

    with pytest.raises(Refusal) as refused:
        gates.reviewed_head(248)

    assert str(refused.value) == f"HEAD {head} is not the last reviewed commit {wanted}; review the branch again"


def test_reviewed_head_refuses_in_a_clone_with_no_ref_even_with_a_reviewed_comment_on_the_issue(
    fake_gh, repo, monkeypatch
):
    """The comment is for whoever reads the issue afterwards; the gate reads the ref, never the log."""
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data["comments"].append(
        {
            "author": {"login": "claude"},
            "createdAt": "2026-09-03T10:00:00Z",
            "body": f"Reviewed: {_sha(repo, 'HEAD')} clean pass",
        }
    )
    path = repo.parent / "issue-reviewed.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_ISSUE_FILE", str(path))

    with pytest.raises(Refusal) as refused:
        gates.reviewed_head(248)

    assert "in this clone" in str(refused.value)


def test_docs_audit_passes_a_doc_only_behind_what_it_cites(repo):
    (repo / "lib").mkdir()
    (repo / "lib" / "thing.py").write_text("x = 1\n")
    (repo / "docs" / "claude").mkdir(parents=True)
    (repo / "docs" / "claude" / "a.md").write_text("See `lib/thing.py`.\n")
    _commit(repo, "add doc")
    (repo / "lib" / "thing.py").write_text("x = 2\n")
    _commit(repo, "change thing")

    assert gates.docs_audit() is None


def test_docs_audit_refuses_a_broken_reference_and_a_duplicate_heading(repo):
    (repo / "docs" / "claude").mkdir(parents=True)
    (repo / "docs" / "claude" / "a.md").write_text("## Setup\n\nSee `lib/missing.py`.\n")
    (repo / "docs" / "claude" / "b.md").write_text("## Setup\n")
    _commit(repo, "add docs")

    with pytest.raises(Refusal) as refused:
        gates.docs_audit()

    lines = str(refused.value).splitlines()
    assert lines[0] == "docs audit found broken references or duplicate headings; run /deckhand:document audit"
    assert "  docs/claude/a.md:" in lines
    assert "    broken reference: `lib/missing.py` (a file in another repository is a link)" in lines
    assert '    duplicate heading "Setup" also in docs/claude/b.md' in lines
