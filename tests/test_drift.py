from __future__ import annotations

import subprocess

from deckhand import drift


def test_references_lists_each_backticked_path_once_and_sorted():
    text = "Modify `a/b.go:12`, then `a/b.go`, then `a/b.go:12` again, and see `https://x.dev/a.b`."

    assert drift.references(text) == ["a/b.go", "a/b.go:12"]


def test_references_ignores_a_version():
    text = "Tag `v1.1.0` after `2.0.0` and `1.2`, and touch `pkg/a.go`."

    assert drift.references(text) == ["pkg/a.go"]


def test_references_ignores_a_bare_name_and_a_dotted_identifier():
    """A plan is full of `errors.As` and `server.go`; neither names a place in the tree."""
    text = "Call `errors.As` on `time.Duration`, edit `server.go` and `router_test.go`, keep `a/b.go`."

    assert drift.references(text) == ["a/b.go"]


def test_references_ignores_a_branch_name_that_carries_a_version():
    text = "Branch `chore/release-back-end-v1.1.0-front-end-v0.4.0` off `main`, then edit `scripts/release.sh`."

    assert drift.references(text) == ["scripts/release.sh"]


def test_drift_text_lists_references_that_do_not_resolve(tmp_path):
    (tmp_path / "internal" / "store").mkdir(parents=True)
    (tmp_path / "internal" / "store" / "collection.go").write_text("a\nb\nc\n")
    text = (
        "- Modify: `internal/store/collection.go:2`\n"
        "- Modify: `internal/store/collection.go:40-80`\n"
        "- Modify: `internal/server/handlers/collections/list.go`\n"
        "- See `https://example.com/a.b`\n"
    )

    assert drift.drift_text(text, tmp_path) == [
        ("internal/server/handlers/collections/list.go", "missing"),
        ("internal/store/collection.go:40-80", "line 40 beyond end of file (3 lines)"),
    ]


def test_drift_text_skips_a_file_the_plan_creates(tmp_path):
    text = "- Create: `scripts/release.sh`\n- Modify: `.github/workflows/ci.yaml`\n"

    assert drift.drift_text(text, tmp_path) == [(".github/workflows/ci.yaml", "missing")]


def test_drift_text_skips_a_created_file_named_again_with_a_line(tmp_path):
    text = "- Create: `a/b.go`\n- Then `a/b.go:12` holds the filter\n"

    assert drift.drift_text(text, tmp_path) == []


def test_drift_text_reads_create_in_any_case_and_without_the_bullet(tmp_path):
    text = "Create: `a/a.go`\n  - CREATE: `b/b.go`\n"

    assert drift.drift_text(text, tmp_path) == []


def test_drift_text_still_flags_a_file_only_mentioned_after_a_create_line(tmp_path):
    text = "- Create: `a/a.go`\n- Modify: `a/b.go`\n"

    assert drift.drift_text(text, tmp_path) == [("a/b.go", "missing")]


def test_drift_text_reads_a_plan_that_was_never_written_to_a_file(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.go").write_text("x\n")

    assert drift.drift_text("Touch `pkg/a.go`, then create `pkg/b.go`\n", tmp_path) == [("pkg/b.go", "missing")]


def test_drift_text_is_empty_when_everything_resolves(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.go").write_text("x\n")

    assert drift.drift_text("Touch `pkg/a.go:1`\n", tmp_path) == []


def test_drift_text_is_empty_when_the_plan_names_no_references(tmp_path):
    assert drift.drift_text("No file paths here, just prose.\n", tmp_path) == []


def test_drift_text_ignores_the_versions_a_release_plan_names(tmp_path):
    text = "Bump to `1.2.3`, tag `v2.0.0`, and read `docs/release.md`.\n"

    assert drift.drift_text(text, tmp_path) == [("docs/release.md", "missing")]


def test_drift_text_checks_paths_with_at_and_plus(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "c+d.go").write_text("x\n")

    assert drift.drift_text("Create `x/a@b.go` and `x/c+d.go`\n", tmp_path) == [("x/a@b.go", "missing")]


def test_drift_text_resolves_a_path_written_relative_to_its_package(tmp_path):
    """A plan says `handlers/list.go` while discussing internal/server; the tracked files know where that is."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "internal" / "server" / "handlers").mkdir(parents=True)
    (tmp_path / "internal" / "server" / "handlers" / "list.go").write_text("a\nb\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    text = "Modify `handlers/list.go:2`, `handlers/list.go:9`, and `handlers/gone.go`\n"

    assert drift.drift_text(text, tmp_path) == [
        ("handlers/gone.go", "missing"),
        ("handlers/list.go:9", "line 9 beyond end of file (2 lines)"),
    ]
