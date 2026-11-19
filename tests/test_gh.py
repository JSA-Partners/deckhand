from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import gh


class Fake:
    def __init__(self, *replies: tuple[int, str, str]) -> None:
        self.calls: list[tuple[list[str], Path | None, str | None]] = []
        self.replies = list(replies)

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append((argv, cwd, stdin))
        code, out, err = self.replies.pop(0) if self.replies else (0, "", "")
        return subprocess.CompletedProcess(argv, code, out, err)

    @property
    def argvs(self) -> list[list[str]]:
        return [c[0] for c in self.calls]


PR = {"number": 7, "url": "u", "isDraft": True, "headRefOid": "h"}
REPO = "acme/widgets"


def test_pr_for_returns_first_or_none() -> None:
    fake = Fake((0, json.dumps([PR]), ""), (0, "[]", ""))
    assert gh.pr_for("b", REPO, fake) == PR
    assert gh.pr_for("b", REPO, fake) is None
    assert fake.argvs[0] == [
        "gh", "pr", "list", "--repo", REPO, "--head", "b", "--state", "open",
        "--json", "number,url,isDraft,headRefOid", "--limit", "1",
    ]  # fmt: skip


def test_ensure_draft_pr_creates_when_missing() -> None:
    fake = Fake((0, "[]", ""), (0, "", ""), (0, json.dumps([PR]), ""))
    assert gh.ensure_draft_pr("b", REPO, "T", "B", fake) == PR
    assert fake.argvs[1] == [
        "gh", "pr", "create", "--repo", REPO, "--draft", "--head", "b", "--title", "T",
        "--body", "B",
    ]  # fmt: skip


def test_ensure_draft_pr_reuses_an_open_one() -> None:
    fake = Fake((0, json.dumps([PR]), ""))
    assert gh.ensure_draft_pr("b", REPO, "T", "B", fake) == PR
    assert len(fake.calls) == 1


def test_mark_ready_argv() -> None:
    fake = Fake()
    gh.mark_ready(7, REPO, fake)
    assert fake.argvs == [["gh", "pr", "ready", "7", "--repo", REPO]]


def rows(*pairs: tuple[str, str]) -> tuple[int, str, str]:
    return 0, json.dumps([{"name": n, "bucket": b} for n, b in pairs]), ""


@pytest.mark.parametrize(
    ("reply", "state", "failing"),
    [
        (rows(("a", "pass"), ("b", "skipping")), "pass", []),
        (rows(("a", "pass"), ("b", "pending")), "pending", []),
        (rows(("a", "fail"), ("b", "pending"), ("c", "cancel")), "fail", ["a", "c"]),
        ((8, json.dumps([{"name": "a", "bucket": "pending"}]), ""), "pending", []),
        ((1, "", "no required checks reported on the 'b' branch"), "pass", []),
        ((1, "", "no checks reported on the 'b' branch"), "pass", []),
        ((0, "[]", ""), "pass", []),
    ],
)
def test_checks_states(reply, state, failing) -> None:
    fake = Fake(reply)
    result = gh.checks(7, REPO, fake)
    assert (result.state, result.failing) == (state, failing)
    assert fake.argvs == [["gh", "pr", "checks", "7", "--repo", REPO, "--required", "--json", "name,bucket"]]


def test_checks_other_failure_raises() -> None:
    with pytest.raises(gh.GhError):
        gh.checks(7, REPO, Fake((1, "", "HTTP 502")))


def test_merge_pinned_argv() -> None:
    fake = Fake()
    gh.merge_pinned(7, REPO, "abc", fake)
    assert fake.argvs == [["gh", "pr", "merge", "7", "--repo", REPO, "--squash", "--match-head-commit", "abc"]]


def test_merge_pinned_maps_head_moved() -> None:
    fake = Fake((1, "", "X Pull request is not mergeable: Head branch was modified"))
    with pytest.raises(gh.HeadMoved):
        gh.merge_pinned(7, REPO, "abc", fake)


def test_merge_pinned_other_failure_is_gh_error() -> None:
    with pytest.raises(gh.GhError) as caught:
        gh.merge_pinned(7, REPO, "abc", Fake((1, "", "boom")))
    assert not isinstance(caught.value, gh.HeadMoved)


def test_repo_name_reads_name_with_owner() -> None:
    fake = Fake((0, json.dumps({"nameWithOwner": REPO}), ""))
    assert gh.repo_name(Path("/clone"), fake) == REPO
    assert fake.calls == [(["gh", "repo", "view", "--json", "nameWithOwner"], Path("/clone"), None)]


def test_gh_error_carries_no_secret() -> None:
    fake = Fake((1, "", "bad credentials ghp_SECRET123 and lin_api_SECRET456"))
    with pytest.raises(gh.GhError) as caught:
        gh.mark_ready(7, REPO, fake)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["gh", "pr"], 120), FileNotFoundError("gh")])
@pytest.mark.parametrize("call", [lambda: gh.mark_ready(7, REPO), lambda: gh.checks(7, REPO)])
def test_default_runner_failures_become_gh_error(error: Exception, call, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(gh.GhError):
        call()
