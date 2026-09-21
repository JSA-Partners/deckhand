"""The lines a context prints to say what to run next."""

from __future__ import annotations

from deckhand import invoke


def test_apply_line_names_the_step_and_its_parts():
    line = invoke.apply_line("finish", "248", "<summary>", '--check "pnpm test"')

    assert line == 'Apply: deckhand finish apply 248 <summary> --check "pnpm test"'


def test_apply_line_with_no_parts_is_the_bare_apply():
    assert invoke.apply_line("update", "248") == "Apply: deckhand update apply 248"


def test_stage_line_lists_every_path():
    line = invoke.stage_line(["internal/auth/a.go", "src/lib/b.ts"])

    assert line == "Stage: git add -- internal/auth/a.go src/lib/b.ts"


def test_stage_line_quotes_a_path_that_needs_it():
    line = invoke.stage_line(["a file.go", "plain.go"])

    assert line == "Stage: git add -- 'a file.go' plain.go"


def test_stage_line_with_no_paths_is_empty():
    assert invoke.stage_line([]) == ""
