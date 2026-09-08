from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, run_deckhand

APPROVED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-approved.json")}
BRANCH = "feat/248-guest-users-see-only"
TITLE = "Guest users see only their granted collections"
LONG_TITLE = "Guest users see only the collections their organization granted them today"
STORY = (
    "As a guest user, I want to see only the collections I was granted, so that I am not exposed to other"
    "\norganizations' data."
)
BODY = f"{STORY}\n\nCloses #248\n"
PR_VIEW = f"pr view {BRANCH} --repo acme/widgets --json url"
PR_URL = "https://github.com/acme/widgets/pull/1000"
PENDING = (
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS "
    "--single-select-option-id opt_pending"
)
ACTUAL = "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTF_ACTUAL --number 3"
NEXT = "Next: merge. Merging closes the issue, and the project's built-in automation sets Done."
REVIEW_ORIGIN = "Branch review: tuicr -r origin/main..HEAD --stdout"
REVIEW_LOCAL = "Branch review: tuicr -r main..HEAD --stdout"
FIRST = "feat(store): list collections by grant"
SECOND = "test(store): cover the grant filter"
RAN = "Running true"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _sha(path: Path, ref: str) -> str:
    return _git(path, "rev-parse", ref).strip()


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", message)


def _writes(gh_calls) -> list[str]:
    return [call for call in gh_calls() if "item-edit" in call or call.startswith("pr create")]


def _fieldvalues(tmp_path: Path, kind: str | None) -> dict[str, str]:
    """The field values fixture with `kind` as the Kind, or with Kind unset when it is None."""
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    values = data["data"]["node"]["fieldValues"]
    values["nodes"] = [node for node in values["nodes"] if (node.get("field") or {}).get("name") != "Kind"]
    if kind is not None:
        values["nodes"].append({"name": kind, "field": {"name": "Kind"}})
    path = tmp_path / f"fieldvalues-{kind or 'nokind'}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_FIELDVALUES_FILE": str(path)}


def _create_call(title: str, body: str) -> str:
    """The `pr create` line the fake records; it joins argv and flattens every newline to a space."""
    args = ["pr", "create", "--repo", "acme/widgets", "--base", "main", "--head", BRANCH]
    return " ".join([*args, "--title", title, "--body", body]).replace("\n", " ")


def _issue(tmp_path: Path, name: str, **changes: str) -> dict[str, str]:
    """The approved-issue fixture with `changes` applied, as the file the fake gh answers from."""
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / f"issue-{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so the story branch has somewhere to be pushed."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare


@pytest.fixture
def branch(repo: Path, origin: Path) -> str:
    """The story branch: two conventional commits, of which origin has only the first.

    Origin deliberately trails HEAD, so every refusal can assert that origin's tip never moved.
    """
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _commit(repo, "store.py", "def by_grant():\n    return []\n", FIRST)
    _git(repo, "push", "-q", "-u", "origin", BRANCH)
    _commit(repo, "store_test.py", "def test_by_grant():\n    pass\n", SECOND)
    return BRANCH


def _clone(origin: Path, path: Path, ref: str) -> Path:
    """A clone checked out on `ref`, which for a branch other than main leaves no local main."""
    subprocess.run(["git", "clone", "-q", "--branch", ref, str(origin), str(path)], check=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    return path


@pytest.fixture
def clone(origin: Path, branch: str, tmp_path: Path) -> Path:
    """The story branch as a colleague would have it: cloned, with no local main anywhere."""
    return _clone(origin, tmp_path / "clone", BRANCH)


def _finish(verb: str, repo: Path, *args: str, env: dict[str, str] | None = None):
    return run_deckhand("finish", verb, "248", *args, cwd=repo, env={**APPROVED, **(env or {})})


def _apply(repo: Path, *extra: str, approved: str | None = None, env: dict[str, str] | None = None):
    args = ["--actual", "3", "--approved", approved if approved is not None else _sha(repo, "HEAD"), *extra]
    if "--check" not in args:
        args += ["--check", "true"]
    return _finish("apply", repo, *args, env=env)


# --- context ----------------------------------------------------------------


def test_context_prints_commits_stat_checks_and_the_review_command(fake_gh, repo, branch):
    result = _finish("context", repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    commits = lines[lines.index("## Commits") + 1 : lines.index("## Diff stat")]
    assert commits == [
        f"  {_sha(repo, 'HEAD~1')[:7]} {FIRST}",
        "  store.py",
        f"  {_sha(repo, 'HEAD')[:7]} {SECOND}",
        "  store_test.py",
    ]
    stat = lines[lines.index("## Diff stat") + 1 : lines.index("## Pull request")]
    assert any("store.py" in line for line in stat)
    assert lines[lines.index("## Checks detected") + 1 :] == ["  none detected", REVIEW_ORIGIN]


def test_context_prints_the_pull_request_block(fake_gh, repo, branch):
    result = _finish("context", repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines.index("## Pull request") > lines.index("## Diff stat")
    said = lines[lines.index("## Pull request") + 1 : lines.index("## Checks detected")]
    assert said == [f"  Title: feat: {TITLE}", *[f"  {line}".rstrip() for line in BODY.splitlines()]]


@pytest.mark.parametrize(
    "broken",
    [lambda tmp_path: _fieldvalues(tmp_path, None), lambda tmp_path: {"GH_ISSUE_VIEW_FAILS": "248"}],
    ids=["no-kind", "unreadable-issue"],
)
def test_context_reports_a_pull_request_it_cannot_compute(fake_gh, repo, branch, tmp_path, broken):
    result = _finish("context", repo, env=broken(tmp_path))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    said = lines[lines.index("## Pull request") + 1 : lines.index("## Checks detected")]
    assert len(said) == 1 and said[0].startswith("  unavailable (")
    assert lines[lines.index("## Commits") + 1].startswith("  ") and lines[-1] == REVIEW_ORIGIN


def test_context_detects_checks_from_project_files(fake_gh, repo, branch):
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "vitest", "lint": "eslint ."}}))
    (repo / "Makefile").write_text("test:\n\tgo test ./...\n")

    result = _finish("context", repo)

    lines = result.stdout.splitlines()
    assert lines[lines.index("## Checks detected") + 1 : lines.index(REVIEW_ORIGIN)] == [
        "  uv run pre-commit run --all-files",
        "  uv run pytest -q",
        "  npm run test",
        "  npm run lint",
        "  make test",
    ]


def test_context_reads_the_branch_in_a_clone_that_has_no_local_main(fake_gh, clone):
    assert _git(clone, "branch", "--format=%(refname:short)").split() == [BRANCH]

    result = _finish("context", clone)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Commits") + 1 : lines.index("## Diff stat")] == [
        f"  {_sha(clone, 'HEAD')[:7]} {FIRST}",
        "  store.py",
    ]
    assert lines[-1] == REVIEW_ORIGIN


def test_context_falls_back_to_local_main_without_an_origin(fake_gh, repo):
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _commit(repo, "store.py", "def by_grant():\n    return []\n", FIRST)

    result = _finish("context", repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Commits") + 1] == f"  {_sha(repo, 'HEAD')[:7]} {FIRST}"
    assert lines[-1] == REVIEW_LOCAL


def test_context_never_fails(fake_gh, repo, tmp_path):
    # A PATH holding only python3: bin/deckhand still starts, and neither git nor gh is on it.
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    (bare / "python3").symlink_to(sys.executable)

    result = run_deckhand("finish", "context", "248", cwd=repo, env={"PATH": str(bare)})

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert [line for line in lines if not line.startswith("  ")] == [
        "## Commits",
        "## Diff stat",
        "## Pull request",
        "## Checks detected",
        REVIEW_LOCAL,
    ]
    assert lines[1].startswith("  unavailable (")


# --- the gates --------------------------------------------------------------


def test_apply_refuses_without_a_check(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)

    result = _finish("apply", repo, "--actual", "3", "--approved", _sha(repo, "HEAD"))

    assert result.returncode != 0
    assert "--check" in result.stderr
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_bad_actual(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, "--actual", "three")

    assert result.returncode == 1
    assert result.stderr == "deckhand finish apply: actual must be a non-negative integer, got 'three'\n"
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_when_head_moved(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, approved=_sha(repo, "HEAD~1"))

    assert result.returncode == 1
    assert result.stderr == (
        f"deckhand finish apply: HEAD moved since approval: expected {_sha(repo, 'HEAD~1')}, got {_sha(repo, 'HEAD')}\n"
    )
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_an_approved_it_cannot_resolve(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, approved="the-one-i-showed-you")

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand finish apply: cannot resolve --approved the-one-i-showed-you: ")
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_dirty_tree(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)
    (repo / "store.py").write_text("def by_grant():\n    return [1]\n")

    result = _apply(repo)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        "deckhand finish apply: the working tree is not clean",
        "   M store.py",
    ]
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_branch_behind_main(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.py", "x = 1\n", "chore: unrelated change")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", BRANCH)

    result = _apply(repo)

    assert result.returncode == 1
    assert result.stderr == "deckhand finish apply: branch does not contain main; rebase first\n"
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_an_unconventional_commit(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)
    _commit(repo, "notes.py", "# notes\n", "made the thing work")
    short = _sha(repo, "HEAD")[:7]

    result = _apply(repo)

    assert result.returncode == 1
    assert result.stderr == (
        f"deckhand finish apply: commit {short}: not a conventional commit subject: made the thing work\n"
    )
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_story_number_in_a_subject(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)
    _commit(repo, "notes.py", "# notes\n", "fix(store): close #248")
    short = _sha(repo, "HEAD")[:7]

    result = _apply(repo)

    assert result.returncode == 1
    assert result.stderr == (
        f"deckhand finish apply: commit {short}: a story number in the subject: fix(store): close #248\n"
    )
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_ignores_what_main_gained_after_the_branch_started(fake_gh, gh_calls, repo, origin, branch):
    # A colleague's merge commit reaches origin/main, and the branch takes it on by rebasing. Local
    # main is still at the branch point, so only a range against origin/main sees the branch's own work.
    _git(repo, "checkout", "-q", "-b", "colleague", "main")
    _commit(repo, "other.py", "x = 1\n", "chore: colleague change")
    _git(repo, "checkout", "-q", "-b", "merged", "main")
    _git(repo, "merge", "-q", "--no-ff", "colleague", "-m", "Merge pull request #12 from acme/colleague")
    _git(repo, "push", "-q", "origin", "merged:main")
    _git(repo, "checkout", "-q", BRANCH)
    _git(repo, "fetch", "-q", "origin", "main")
    _git(repo, "rebase", "-q", "origin/main")

    result = _apply(repo)

    assert result.returncode == 0, result.stderr
    assert f"Opened {PR_URL}" in result.stdout
    assert _sha(origin, BRANCH) == _sha(repo, "HEAD")


def test_apply_refuses_a_failing_check(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, "--check", "true", "--check", "echo out; echo err >&2; exit 1")

    assert result.returncode == 1
    assert result.stdout.splitlines() == [RAN, "Running echo out; echo err >&2; exit 1"]
    assert result.stderr.splitlines() == [
        "deckhand finish apply: check failed: echo out; echo err >&2; exit 1",
        "  out",
        "  err",
    ]
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_stale_docs_audit_before_it_runs_a_check(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)
    (repo / "docs" / "claude").mkdir(parents=True)
    (repo / "docs" / "claude" / "grants.md").write_text("# Grants\n\nThe filter lives in `store/gone.py`.\n")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-qm", "docs(claude): note where the filter lives")

    result = _apply(repo, "--check", "false")

    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert lines[0] == "deckhand finish apply: docs audit found stale files; run /deckhand:document audit"
    assert any("broken reference: `store/gone.py`" in line for line in lines[1:])
    assert result.stdout == ""  # the checks are the slow gate, so they run last
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_without_a_kind(fake_gh, gh_calls, repo, origin, branch, tmp_path):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, env=_fieldvalues(tmp_path, None))

    assert result.returncode == 1
    assert result.stderr == "deckhand finish apply: #248 has no Kind; run /deckhand:ready 248\n"
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_title_over_72(fake_gh, gh_calls, repo, origin, branch, tmp_path):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, env=_issue(tmp_path, "long-title", title=LONG_TITLE))

    assert result.returncode == 1
    assert result.stderr == ("deckhand finish apply: title too long for a commit subject; shorten the issue title\n")
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_story_without_a_story_section(fake_gh, gh_calls, repo, origin, branch, tmp_path):
    tip = _sha(origin, BRANCH)
    body = "### Scope\n\n#### In\n\n- The grant filter\n\n### Notes\n\nNone\n"

    result = _apply(repo, env=_issue(tmp_path, "no-story", body=body))

    assert result.returncode == 1
    assert result.stderr == "deckhand finish apply: #248 has no Story section; run /deckhand:amend 248\n"
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


def test_apply_refuses_a_breaking_with_no_text(fake_gh, gh_calls, repo, origin, branch):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, "--breaking", "   ")

    assert result.returncode == 1
    assert result.stderr == "deckhand finish apply: --breaking needs the text a client must react to\n"
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


@pytest.mark.parametrize("flag", ["--lockstep", "--migration"])
def test_apply_rejects_lockstep_and_migration(fake_gh, gh_calls, repo, origin, branch, flag):
    tip = _sha(origin, BRANCH)

    result = _apply(repo, flag, "acme/api#7")

    assert result.returncode == 2
    assert flag in result.stderr
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == tip


# --- the writes -------------------------------------------------------------


def test_apply_pushes_opens_the_pr_and_sets_the_fields(fake_gh, gh_calls, repo, origin, branch):
    result = _apply(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        RAN,
        "Pushed",
        f"Opened {PR_URL}",
        "Status=Pending Review",
        "Actual=3",
        NEXT,
    ]
    assert _sha(origin, BRANCH) == _sha(repo, "HEAD")
    calls = [call for call in gh_calls() if "item-edit" in call or call.startswith(("issue view", "pr "))]
    assert [" ".join(call.split()[:2]) for call in calls] == [
        "issue view",
        "pr view",
        "pr create",
        "project item-edit",
        "project item-edit",
    ]
    assert calls[1] == PR_VIEW
    assert calls[2].startswith("pr create ")
    assert calls[3:] == [PENDING, ACTUAL]


def test_apply_uses_the_kind_in_the_title(fake_gh, gh_calls, repo, origin, branch, tmp_path):
    result = _apply(repo, env=_fieldvalues(tmp_path, "fix"))

    assert result.returncode == 0, result.stderr
    create = next(call for call in gh_calls() if call.startswith("pr create"))
    assert create.startswith(f"pr create --repo acme/widgets --base main --head {BRANCH} --title fix: {TITLE} ")


def test_apply_opens_the_pull_request_with_the_computed_message(fake_gh, gh_calls, repo, origin, branch):
    result = _apply(repo)

    assert result.returncode == 0, result.stderr
    create = next(call for call in gh_calls() if call.startswith("pr create"))
    assert create == _create_call(f"feat: {TITLE}", BODY)
    assert "--body-file" not in create


def test_apply_adds_the_bang_and_the_footer_when_breaking(fake_gh, gh_calls, repo, origin, branch):
    result = _apply(repo, "--breaking", "The response shape changes for guests.")

    assert result.returncode == 0, result.stderr
    create = next(call for call in gh_calls() if call.startswith("pr create"))
    assert create == _create_call(
        f"feat!: {TITLE}",
        f"{STORY}\n\nBREAKING CHANGE: The response shape changes for guests.\nCloses #248\n",
    )


def test_apply_reuses_a_pull_request_the_branch_already_has(fake_gh, gh_calls, repo, origin, branch):
    result = _apply(repo, env={"GH_PR_EXISTS": "1"})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        RAN,
        "Pushed",
        f"Reusing {PR_URL}",
        "Status=Pending Review",
        "Actual=3",
        NEXT,
    ]
    assert [call for call in gh_calls() if call.startswith("pr create")] == []
    assert [call for call in gh_calls() if "item-edit" in call] == [PENDING, ACTUAL]


def test_apply_pushes_a_branch_that_tracks_nothing(fake_gh, repo, origin):
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _commit(repo, "store.py", "def by_grant():\n    return []\n", FIRST)
    _git(repo, "push", "-q", "origin", BRANCH)  # no -u, so the branch tracks nothing
    _commit(repo, "store_test.py", "def test_by_grant():\n    pass\n", SECOND)

    result = _apply(repo)

    assert result.returncode == 0, result.stderr
    assert f"Opened {PR_URL}" in result.stdout
    assert _sha(origin, BRANCH) == _sha(repo, "HEAD")


def test_apply_works_in_a_clone_that_has_no_local_main(fake_gh, gh_calls, repo, origin, branch, clone):
    result = _apply(clone)

    assert result.returncode == 0, result.stderr
    assert f"Opened {PR_URL}" in result.stdout
    assert [call for call in gh_calls() if "item-edit" in call] == [PENDING, ACTUAL]


def test_apply_stops_at_the_push_when_origin_moved_ahead(fake_gh, gh_calls, repo, origin, branch, tmp_path):
    colleague = _clone(origin, tmp_path / "colleague", BRANCH)
    _commit(colleague, "theirs.py", "y = 2\n", "fix(store): a colleague's fix")
    _git(colleague, "push", "-q", "origin", BRANCH)
    theirs = _sha(origin, BRANCH)
    _git(repo, "fetch", "-q", "origin")  # an ambient fetch: the lease alone would no longer protect them

    result = _apply(repo)

    assert result.returncode == 1
    assert result.stdout.splitlines() == [RAN]
    assert result.stderr.startswith("deckhand finish: ") and "rejected" in result.stderr
    assert _writes(gh_calls) == []
    assert _sha(origin, BRANCH) == theirs
