from __future__ import annotations

from deckhand import drift


def test_references_lists_each_backticked_path_once_and_sorted():
    text = "Modify `a/b.go:12`, then `a/b.go`, then `a/b.go:12` again, and see `https://x.dev/a.b`."

    assert drift.references(text) == ["a/b.go", "a/b.go:12"]


def test_drift_text_lists_references_that_do_not_resolve(tmp_path):
    (tmp_path / "internal" / "store").mkdir(parents=True)
    (tmp_path / "internal" / "store" / "collection.go").write_text("a\nb\nc\n")
    text = (
        "- Modify: `internal/store/collection.go:2`\n"
        "- Modify: `internal/store/collection.go:40-80`\n"
        "- Create: `internal/server/handlers/collections/list.go`\n"
        "- See `https://example.com/a.b`\n"
    )

    assert drift.drift_text(text, tmp_path) == [
        ("internal/server/handlers/collections/list.go", "missing"),
        ("internal/store/collection.go:40-80", "line 40 beyond end of file (3 lines)"),
    ]


def test_drift_text_reads_a_plan_that_was_never_written_to_a_file(tmp_path):
    (tmp_path / "a.go").write_text("x\n")

    assert drift.drift_text("Touch `a.go`, then create `b.go`\n", tmp_path) == [("b.go", "missing")]


def test_drift_text_is_empty_when_everything_resolves(tmp_path):
    (tmp_path / "a.go").write_text("x\n")

    assert drift.drift_text("Touch `a.go:1`\n", tmp_path) == []


def test_drift_text_is_empty_when_the_plan_names_no_references(tmp_path):
    assert drift.drift_text("No file paths here, just prose.\n", tmp_path) == []


def test_drift_text_checks_paths_with_at_and_plus(tmp_path):
    (tmp_path / "c+d.go").write_text("x\n")

    assert drift.drift_text("Create `a@b.go` and `c+d.go`\n", tmp_path) == [("a@b.go", "missing")]
