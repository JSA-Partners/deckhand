from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand import git, tuicr
from deckhand.policy import Policy
from deckhand.summary import ChangeSummary, NewHere

BASE_FILES = {
    "src/auth.py": "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n",
    "src/pay.py": "p1\np2\np3\np4\np5\np6\np7\np8\np9\np10\n",
    "src/util.py": "u1\nu2\n",
    "db/migrations/001.sql": "m\n",
    "gone.txt": "bye\n",
}


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path | None, str | None]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append((argv, cwd, stdin))
        return subprocess.CompletedProcess(argv, 0, "", "")


def policy(*sensitive: str) -> Policy:
    return Policy(repository_label="widgets", sensitive_paths=list(sensitive))


MANIFEST = {"contract_sites": [], "invariant_sites": []}


@pytest.fixture
def story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for key, value in (("NAME", "t"), ("EMAIL", "t@t")):
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
    clone = tmp_path / "clone"
    clone.mkdir()

    def git_(*args: str, cwd: Path = clone) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git_("init", "-q", "-b", "main")
    for name, text in BASE_FILES.items():
        (clone / name).parent.mkdir(parents=True, exist_ok=True)
        (clone / name).write_text(text)
    git_("add", ".")
    git_("commit", "-q", "-m", "base")
    work = git.worktree_add(clone, "story", "main")
    (work / "src/auth.py").write_text("A\nb\nc\nd\ne\nf\ng\nh\ni\nJ\n")
    (work / "src/pay.py").write_text("P1\np2\np3\np4\np5\np6\np7\np8\np9\nP10\n")
    (work / "src/util.py").write_text("u1\nu2\nu3\n")
    (work / "db/migrations/002.sql").write_text("new\n")
    (work / "gone.txt").unlink()
    git_("add", "-A", cwd=work)
    git_("commit", "-q", "-m", "story", cwd=work)
    return clone, work


def story_diff(work: Path) -> str:
    return git.diff(work, "main")


def reasons(hunks: list[tuicr.Hunk]) -> dict[tuple[str, str], str]:
    return {(h.path, h.lines[0].split(" @@")[0]): h.reason for h in hunks}


def test_selects_sensitive_paths(story: tuple[Path, Path]) -> None:
    diff = story_diff(story[1])
    hunks = tuicr.select_hunks(diff, policy("db/migrations/*", "src/**/auth.py"), set(), MANIFEST)
    assert sorted({h.path for h in hunks}) == ["db/migrations/002.sql", "src/auth.py"]
    assert all("sensitive" in h.reason for h in hunks)
    assert len([h for h in hunks if h.path == "src/auth.py"]) == 2


def test_selects_doubtful_finding_files(story: tuple[Path, Path]) -> None:
    hunks = tuicr.select_hunks(story_diff(story[1]), policy(), {"src/util.py"}, MANIFEST)
    assert [h.path for h in hunks] == ["src/util.py"]
    assert "doubtful finding" in hunks[0].reason


def test_selects_contract_sites(story: tuple[Path, Path]) -> None:
    manifest = {"contract_sites": ["src/pay.py"], "invariant_sites": ["src/util.py"]}
    hunks = tuicr.select_hunks(story_diff(story[1]), policy(), set(), manifest)
    by_path = {h.path: h.reason for h in hunks}
    assert "contract" in by_path["src/pay.py"]
    assert "invariant" in by_path["src/util.py"]
    assert set(by_path) == {"src/pay.py", "src/util.py"}


def test_selects_new_here_paths(story: tuple[Path, Path]) -> None:
    summary = ChangeSummary([], [NewHere("first queue", "src/util.py")], [], None)
    hunks = tuicr.select_hunks(story_diff(story[1]), policy(), set(), MANIFEST, summary)
    assert [h.path for h in hunks] == ["src/util.py"]
    assert hunks[0].reason == "new here: first queue"


def test_several_reasons_join_into_one(story: tuple[Path, Path]) -> None:
    manifest = {"contract_sites": ["src/util.py"], "invariant_sites": []}
    hunks = tuicr.select_hunks(story_diff(story[1]), policy("src/*.py"), {"src/util.py"}, manifest)
    util = [h for h in hunks if h.path == "src/util.py"]
    assert len(util) == 1
    assert util[0].reason.count("; ") == 2
    assert "sensitive" in util[0].reason and "doubtful" in util[0].reason and "contract" in util[0].reason


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("src/**/auth.py", "src/auth.py", True),
        ("src/**/auth.py", "src/a/b/auth.py", True),
        ("src/**/auth.py", "lib/auth.py", False),
        ("migrations/*", "migrations/001.sql", True),
        ("migrations/*", "migrations/x/001.sql", False),
        ("**/secrets.toml", "a/b/secrets.toml", True),
        ("src/**", "src/a/b.py", True),
        ("a.b", "axb", False),
    ],
)
def test_glob_semantics(pattern: str, path: str, matches: bool) -> None:
    assert tuicr.glob_match(pattern, path) is matches


def test_unselected_hunks_absent_from_review_commit(story: tuple[Path, Path]) -> None:
    clone, work = story
    diff = story_diff(work)
    manifest = {"contract_sites": ["src/pay.py"], "invariant_sites": []}
    hunks = tuicr.select_hunks(diff, policy("db/migrations/*", "gone.txt"), set(), manifest)
    before = git.head(work)
    sha = tuicr.review_commit(work, "main", hunks)

    def out(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=clone, capture_output=True, text=True, check=True).stdout

    assert sorted(out("diff", "--name-only", "main", sha).split()) == [
        "db/migrations/002.sql",
        "gone.txt",
        "src/pay.py",
    ]
    assert "u3" not in out("diff", "main", sha)
    assert "src/auth.py" not in out("diff", "--name-only", "main", sha)
    assert out("rev-parse", "refs/deckhand/review/story").strip() == sha
    assert git.head(work) == before


def test_one_hunk_of_a_file_applies_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = tmp_path / "c"
    clone.mkdir()
    for key, value in (("NAME", "t"), ("EMAIL", "t@t")):
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
    lines = [f"l{i}" for i in range(1, 41)]

    def git_(*args: str, cwd: Path = clone) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git_("init", "-q", "-b", "main")
    (clone / "f.txt").write_text("\n".join(lines) + "\n")
    git_("add", ".")
    git_("commit", "-q", "-m", "base")
    work = git.worktree_add(clone, "story", "main")
    changed = ["top", "l1", "l2", *lines[2:38], "l39", "tail"]
    (work / "f.txt").write_text("\n".join(changed) + "\n")
    git_("add", "-A", cwd=work)
    git_("commit", "-q", "-m", "s", cwd=work)
    hunks = tuicr.select_hunks(
        git.diff(work, "main"), policy(), set(), {"contract_sites": ["f.txt"], "invariant_sites": []}
    )
    assert len(hunks) == 2
    sha = tuicr.review_commit(work, "main", hunks[1:])
    shown = subprocess.run(
        ["git", "show", f"{sha}:f.txt"], cwd=clone, capture_output=True, text=True, check=True
    ).stdout
    assert shown.splitlines()[0] == "l1"
    assert shown.splitlines()[-1] == "tail"


def test_seed_comments_argv() -> None:
    fake = Fake()
    added = tuicr.Hunk("src/a.py", "H", ["@@ -3,2 +3,3 @@", " x", "+y", " z"], "sensitive path src/*.py")
    removed = tuicr.Hunk("old.txt", "H", ["@@ -1 +0,0 @@", "-bye"], "doubtful finding")
    tuicr.seed_comments("slug", [added, removed], Path("/clone"), fake)
    assert fake.calls[0][0] == [
        "tuicr", "review", "add", "--session", "slug", "--repo", "/clone",
        "--target-file", "src/a.py", "--line", "4", "--side", "new",
        "--username", "deckhand", "sensitive path src/*.py",
    ]  # fmt: skip
    assert fake.calls[1][0][-9:-3] == ["--target-file", "old.txt", "--line", "1", "--side", "old"]


def test_seed_comments_numbers_lines_in_the_trimmed_commit() -> None:
    fake = Fake()
    first = tuicr.Hunk("f", "H", ["@@ -1,1 +1,3 @@", " a", "+b", "+c"], "r1")
    second = tuicr.Hunk("f", "H", ["@@ -10,1 +12,2 @@", " j", "+k"], "r2")
    tuicr.seed_comments("s", [second], None, fake)
    assert fake.calls[0][0][fake.calls[0][0].index("--line") + 1] == "11"
    fake.calls.clear()
    tuicr.seed_comments("s", [first, second], None, fake)
    assert fake.calls[1][0][fake.calls[1][0].index("--line") + 1] == "13"


def test_command_is_the_range_argv() -> None:
    assert tuicr.command("abc", "def") == ["tuicr", "-r", "abc..def"]
