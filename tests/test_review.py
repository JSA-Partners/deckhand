from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deckhand import review, sections
from tests.conftest import FIXTURES, ROOT, run_deckhand

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
ISSUE = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
COMMENT_URL = "https://github.com/acme/widgets/issues/248#issuecomment-77"
GUIDANCE = (
    "Tick a finding to accept it; leave it unticked to decline. A finding marked rejected by the "
    "skeptic is shown for the record; tick it only to overrule them. Replies here are read the "
    "next time the story is amended. Then run `/deckhand:next 248`: it applies what you ticked and "
    "replied, or boards the story when you accepted nothing."
)
CLEAN_LINE = "Reply here with anything still wrong, then run `/deckhand:next 248`."
NEXT = "Next: /deckhand:next 248 when you have ticked and replied."


def _withheld(n: int) -> str:
    """The closing count the comment ends on when the cap left `n` confirmed findings off."""
    word = "finding" if n == 1 else "findings"
    return f"{n} further {word} withheld; after amending, choose Review it again when /deckhand:next 248 offers it."


@pytest.fixture
def lenses(tmp_path, monkeypatch):
    """A scratch lenses/ directory, isolated from the real one."""
    root = tmp_path / "lenses"
    root.mkdir()
    monkeypatch.setenv("DECKHAND_LENSES", str(root))
    return root


def _lens(name, signals=None, always=None) -> str:
    """One lens file's text, as `_selected` and `_lens_files` both read it."""
    lines = ["---", f"name: {name}"]
    if signals is not None:
        lines.append(f"signals: [{', '.join(signals)}]")
    if always is not None:
        lines.append(f"always: {'true' if always else 'false'}")
    lines += ["---", "", f"# {name}", "", f"Look at {name}."]
    return "\n".join(lines) + "\n"


def _write_lens(root, name, signals=None, always=None):
    (root / f"{name}.md").write_text(_lens(name, signals, always))


def _issue_file(tmp_path: Path, body: str) -> dict[str, str]:
    """An env that points the fake gh at an issue with `body`."""
    path = tmp_path / "issue.json"
    path.write_text(json.dumps({**ISSUE, "body": body}), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def _gh_without_issue_view(tmp_path: Path) -> dict[str, str]:
    """A gh ahead of the fake that fails `issue view` and hands every other call through."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    script = fake_bin / "gh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2" = "issue view" ]; then echo "could not find issue 248" >&2; exit 1; fi\n'
        f'exec "{ROOT / "tests" / "fakes" / "gh"}" "$@"\n'
    )
    script.chmod(0o755)
    return {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}


def _writes(gh_calls) -> list[str]:
    """The recorded calls that write, with the temp body-file path cut off."""
    starts = ("issue create", "issue edit", "issue comment", "api -X POST")
    return [call.split(" --body-file")[0] for call in gh_calls() if call.startswith(starts)]


def _findings(tmp_path: Path, text: str) -> str:
    path = tmp_path / "248-findings.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _posted(copy: Path) -> str:
    """The comment body `gh issue comment` was handed, without the fake's marker line."""
    text = copy.read_text(encoding="utf-8")
    marker = "--- issue comment\n"
    assert text.startswith(marker)
    return text[len(marker) :]


# --- lens frontmatter and selection -----------------------------------------


def test_every_real_lens_has_valid_frontmatter():
    """A lens declares only what selection reads; anything else would be a rule nothing enforces."""
    lens_files = sorted((ROOT / "skills" / "review" / "lenses").glob("*.md"))
    assert len(lens_files) == 7
    for path in lens_files:
        text = path.read_text()
        assert text.splitlines()[0] == "---"
        fm = review._frontmatter(text)
        assert set(fm) <= {"name", "signals", "always"}, path.name
        assert fm["name"] == path.stem
        assert fm["signals"].startswith("[") and fm["signals"].endswith("]")
        assert fm.get("always", "true") == "true"


def test_body_text_drops_the_frontmatter_and_the_lenss_own_title():
    text = "---\nname: chaos\nalways: true\n---\n\n# Chaos\n\nVary the events.\n\n# Not a title\n"

    assert review._body_text(text) == "Vary the events.\n\n# Not a title"


def test_selected_takes_the_always_on_and_the_signal_matched():
    lenses = {
        "always-on": _lens("always-on", signals=["nothing"], always=True),
        "matched": _lens("matched", signals=["grant"]),
        "quiet": _lens("quiet", signals=["webhook"]),
    }

    assert review._selected(lenses, "A guest with one grant lists collections.") == ["always-on", "matched"]


def test_selected_adds_a_named_lens_that_no_signal_matched():
    lenses = {"quiet": _lens("quiet", signals=["webhook"]), "missing": _lens("missing", signals=["webhook"])}

    assert review._selected(lenses, "nothing here", ["quiet", "not-a-lens"]) == ["quiet"]


def test_selected_signal_matching_is_whole_word():
    assert review._selected({"one": _lens("one", signals=["auth"])}, "authoritative guests") == []


def test_selected_accepts_quoted_and_commented_frontmatter_values():
    quoted = '---\nname: "quoted"\nalways: Yes # every story\nsignals: [x] # one\n---\n\n# q\n'

    assert review._selected({"quoted": quoted}, "nothing that matches") == ["quoted"]


def test_lens_files_reads_every_lens_in_name_order(lenses):
    _write_lens(lenses, "zeta", signals=["x"])
    _write_lens(lenses, "alpha", signals=["x"])

    assert list(review._lens_files()) == ["alpha", "zeta"]


def test_named_lenses_reads_the_notes_line():
    body = "### Story\n\nx\n\n### Notes\n\n- Lenses: chaos, pen-test\n"

    assert review.named_lenses(body) == ["chaos", "pen-test"]
    assert review.named_lenses("### Story\n\nx\n") == []


def test_named_lenses_takes_the_first_word_of_an_entry():
    body = "### Story\n\nx\n\n### Notes\n\nLenses: chaos (retry path), pen-test\n"

    assert review.named_lenses(body) == ["chaos", "pen-test"]


# --- context ----------------------------------------------------------------


def test_context_selects_always_and_signal_lenses(fake_gh):
    result = run_deckhand("review", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "### Story"
    assert review.BRIEF_HEADING in lines
    headings = [line for line in lines if line.startswith("### ") and line[4:] in {p.stem for p in _lens_files()}]
    assert headings == ["### coverage", "### pen-test", "### principles", "### red-team", "### unknowns"]
    # the lens prose, with its frontmatter and its own title stripped
    assert "Read the artifact as a motivated adversary" in result.stdout
    assert "signals:" not in result.stdout
    assert "# Red team" not in result.stdout


def _lens_files():
    return sorted((ROOT / "skills" / "review" / "lenses").glob("*.md"))


def test_context_adds_a_lens_named_in_notes(fake_gh, tmp_path):
    body = ISSUE["body"] + "\n- Lenses: chaos\n"

    result = run_deckhand("review", "context", "248", env=_issue_file(tmp_path, body))

    assert result.returncode == 0, result.stderr
    assert "### chaos" in result.stdout.splitlines()
    assert "State the steady state the story assumes" in result.stdout


def test_context_prints_the_finding_format_and_the_findings_path(fake_gh, tmp_path):
    result = run_deckhand("review", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-2] == (
        "Report findings as lines: <lens>.<n> | P1|P2|P3 | PENDING | <claim> | "
        "<evidence, citing the section>; or exactly `Nothing found.`"
    )
    assert lines[-2] == review.FINDING_FORMAT
    assert lines[-1] == f"Findings: {tmp_path / 'cache' / 'widgets' / '248-findings.md'}"
    assert not (tmp_path / "cache" / "widgets" / "248-findings.md").exists()


def test_context_still_prints_when_the_issue_cannot_be_read(fake_gh, tmp_path):
    result = run_deckhand("review", "context", "248", env=_gh_without_issue_view(tmp_path))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "Body: unavailable (could not find issue 248)"
    assert [line for line in lines if line.startswith("### ")] == [
        "### coverage",
        "### principles",
        "### unknowns",
    ]
    assert lines[-2] == review.FINDING_FORMAT


def test_context_prints_the_plan_out_of_its_fold(fake_gh, tmp_path):
    """The reviewer reads the plan, and on GitHub the plan sits inside a collapsed block."""
    preamble, parsed = sections.parse(ISSUE["body"])
    folded = sections.render(preamble, parsed)
    assert "<details>" in folded

    result = run_deckhand("review", "context", "248", env=_issue_file(tmp_path, folded))

    assert result.returncode == 0, result.stderr
    assert "<details>" not in result.stdout
    assert "</details>" not in result.stdout
    assert "### Task 1: Store method" in result.stdout


# --- apply ------------------------------------------------------------------


def test_apply_refuses_a_stub(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("review", "apply", "57", _findings(tmp_path, "Nothing found.\n"), env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: #57 is a stub; run /deckhand:new 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_a_malformed_line_and_writes_nothing(fake_gh, gh_calls, tmp_path):
    text = "red-team.1 | P1 | CONFIRMED | A guest reads another collection | Scope In\nnot a finding\n"

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text))

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: line 2: expected <lens>.<n> | P1|P2|P3 | CONFIRMED|REJECTED | <claim> | <evidence>\n"
    )
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_a_pipe_inside_a_claim(fake_gh, gh_calls, tmp_path):
    text = "chaos.1 | P1 | CONFIRMED | foo(a | b) is never bounded | Scope In\n"

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text))

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand review apply: line 1: expected ")
    assert _writes(gh_calls) == []


def test_apply_refuses_a_duplicate_finding_id(fake_gh, gh_calls, tmp_path):
    text = (
        "chaos.1 | P1 | CONFIRMED | A retry writes twice | Scope In\n"
        "chaos.1 | P2 | REJECTED | The cache outlives the row | Notes\n"
    )

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 2: chaos.1 is already the id of an earlier finding\n"
    assert _writes(gh_calls) == []


def test_apply_reads_past_a_byte_order_mark(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    path = tmp_path / "248-findings.md"
    path.write_bytes("\ufeffNothing found.\n".encode("utf-8"))

    result = run_deckhand("review", "apply", "248", str(path), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == f"## Review\n\nNothing found.\n\n{CLEAN_LINE}\n"


def test_apply_refuses_an_unknown_lens(fake_gh, gh_calls, tmp_path):
    text = "vibes.1 | P1 | CONFIRMED | It feels wrong | Story\n"

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 1: no lens named 'vibes'\n"
    assert _writes(gh_calls) == []


def test_apply_posts_every_finding_as_a_box(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "comment.md"

    result = run_deckhand(
        "review",
        "apply",
        "248",
        str(FIXTURES / "findings.md"),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if c.startswith("issue comment")]
    assert call.startswith("issue comment 248 --repo acme/widgets --body-file ")
    assert _posted(copy) == (
        "## Review\n"
        "\n"
        f"{GUIDANCE}\n"
        "\n"
        "- [ ] **red-team.1, P1** A guest reads another organization's collection by id. "
        "Scope In names filtering but no ownership check.\n"
        "- [ ] **chaos.1, P2** A retried job writes the grant twice. "
        "Acceptance Criteria says nothing about a repeated list.\n"
        "- [ ] **unknowns.1, P3, rejected by the skeptic** The existing store method is undefined. "
        "Notes points at internal/store/grant.go.\n"
    )
    assert result.stdout.splitlines() == [COMMENT_URL, NEXT]


def test_a_box_ends_the_claim_and_the_evidence_with_a_stop(fake_gh, tmp_path):
    """A finding is two sentences on one line, so neither half may run into what follows it."""
    copy = tmp_path / "comment.md"
    text = "chaos.1 | P2 | CONFIRMED | A retry writes twice | Scope In names one write\n"

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert "**chaos.1, P2** A retry writes twice. Scope In names one write.\n" in _posted(copy)


def test_a_box_adds_no_second_stop_to_a_half_that_has_one(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    text = "chaos.1 | P2 | CONFIRMED | Does a retry write twice? | Scope In names one write.\n"

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert "**chaos.1, P2** Does a retry write twice? Scope In names one write.\n" in _posted(copy)


def test_apply_posts_nothing_found(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"

    result = run_deckhand(
        "review",
        "apply",
        "248",
        str(FIXTURES / "findings-clean.md"),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == f"## Review\n\nNothing found.\n\n{CLEAN_LINE}\n"
    assert result.stdout.splitlines() == [COMMENT_URL, NEXT]


def test_apply_boxes_a_rejected_finding_too(fake_gh, tmp_path):
    """A rejection is the skeptic's, not the reader's, so it is still a box the reader can tick."""
    copy = tmp_path / "comment.md"
    only_rejected = "unknowns.1 | P2 | REJECTED | The store method is undefined | Notes names the file\n"

    result = run_deckhand(
        "review",
        "apply",
        "248",
        _findings(tmp_path, only_rejected),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == (
        "## Review\n"
        "\n"
        f"{GUIDANCE}\n"
        "\n"
        "- [ ] **unknowns.1, P2, rejected by the skeptic** The store method is undefined. "
        "Notes names the file.\n"
    )
    assert result.stdout.splitlines() == [COMMENT_URL, NEXT]


def test_the_guidance_names_both_ends_of_the_next_step():
    """Ticking nothing is an answer, so the guidance says what the one command does either way."""
    assert "or boards the story when you accepted nothing." in review.guidance(248)


def test_the_withheld_line_names_the_choice_that_asks_for_a_second_pass():
    """The rest are had by choosing Review it again at the board question, not by rerunning next."""
    assert "choose Review it again" in review.comment_body(248, [], [], withheld=2)


def test_the_guidance_says_a_reply_is_read_at_the_next_amend():
    """A reply is not lost and not free: the amend step reads it, so the guidance says when."""
    assert "Replies here are read the next time the story is amended." in review.guidance(248)


def test_the_guidance_names_the_issue_the_comment_is_posted_on():
    """The comment stands alone, so the commands in it have to carry the number, not a placeholder."""
    assert review.comment_body(931, [], []) == (
        "## Review\n\nNothing found.\n\nReply here with anything still wrong, then run `/deckhand:next 931`.\n"
    )
    assert "/deckhand:next 931" in review.guidance(931)


def test_apply_refuses_an_empty_findings_file(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("review", "apply", "248", _findings(tmp_path, "\n\n"))

    assert result.returncode == 1
    assert "the findings file is empty" in result.stderr
    assert _writes(gh_calls) == []


def test_apply_heading_is_the_one_amend_looks_for():
    from deckhand import issue as issue_module

    assert review.comment_body(248, [], []).splitlines()[0] == issue_module.REVIEW_HEADING


# --- the cap ----------------------------------------------------------------


def _confirmed(*specs: tuple[str, int, str]) -> str:
    """A findings file from `(lens, ordinal, severity)` triples, every line confirmed."""
    return "".join(f"{lens}.{n} | {sev} | CONFIRMED | Claim {lens}{n} | Section {lens}\n" for lens, n, sev in specs)


def _ids(body: str) -> list[str]:
    """The finding ids the comment boxed, in the order they appear."""
    return [line.split("**")[1].split(",")[0] for line in body.splitlines() if line.startswith("- [ ] **")]


def test_apply_posts_at_most_seven_confirmed_findings_and_counts_the_rest(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    text = _confirmed(
        *[("coverage", n, "P1") for n in (1, 2, 3)],
        *[("chaos", n, "P2") for n in (1, 2, 3, 4)],
        *[("unknowns", n, "P3") for n in (1, 2, 3)],
    )

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    body = _posted(copy)
    assert body.count("- [ ] **") == 7
    assert body.rstrip().endswith(_withheld(3))


def test_the_seven_are_the_most_severe_in_lens_order(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    text = _confirmed(
        ("red-team", 1, "P1"),
        ("chaos", 1, "P1"),
        ("unknowns", 1, "P3"),
        *[("coverage", n, "P2") for n in (1, 2, 3)],
        ("pen-test", 1, "P2"),
        ("principles", 1, "P2"),
    )

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    body = _posted(copy)
    assert _ids(body) == [
        "chaos.1",
        "red-team.1",
        "coverage.1",
        "coverage.2",
        "coverage.3",
        "pen-test.1",
        "principles.1",
    ]
    assert "unknowns.1" not in body
    assert body.rstrip().endswith(_withheld(1))


def test_every_rejected_finding_is_shown_and_none_counts_against_the_cap(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    text = _confirmed(
        *[("coverage", n, "P1") for n in (1, 2, 3, 4)],
        *[("chaos", n, "P2") for n in (1, 2, 3)],
    ) + "".join(f"unknowns.{n} | P2 | REJECTED | Claim u{n} | Notes\n" for n in (1, 2, 3))

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    body = _posted(copy)
    assert body.count("- [ ] **") == 10
    boxes = [line for line in body.splitlines() if line.startswith("- [ ] **")]
    assert len([line for line in boxes if "rejected by the skeptic" in line]) == 3
    assert "withheld" not in body


def test_the_withheld_line_is_absent_when_seven_or_fewer_are_confirmed(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    text = _confirmed(*[("coverage", n, "P2") for n in (1, 2, 3, 4, 5, 6, 7)])

    result = run_deckhand("review", "apply", "248", _findings(tmp_path, text), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    body = _posted(copy)
    assert body.count("- [ ] **") == 7
    assert "withheld" not in body


def test_comment_body_reports_a_count_it_was_given_with_no_findings():
    """The count is the caller's, so it is never swallowed by the clean line."""
    body = review.comment_body(248, [], [], withheld=2)

    assert review.CLEAN not in body
    assert body.rstrip().endswith(_withheld(2))


def test_comment_body_counts_only_what_it_was_told_was_withheld():
    """The count is the caller's, so the comment never has to know what it was not given."""
    one = review.Finding("chaos", 1, "P1", "CONFIRMED", "A retry writes twice", "Scope In")

    assert "withheld" not in review.comment_body(248, [one], [])
    assert review.comment_body(248, [one], [], withheld=1).rstrip().endswith(_withheld(1))
    assert review.comment_body(248, [one], [], withheld=2).rstrip().endswith(_withheld(2))
