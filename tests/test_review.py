from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deckhand import review, sections
from tests.conftest import FIXTURES, ROOT, run_deckhand, spilled

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
ISSUE = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
COMMENT_URL = "https://github.com/acme/widgets/issues/248#issuecomment-77"


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


def _posted(copy: Path) -> str:
    """The comment body `gh issue comment` was handed, without the fake's marker line."""
    text = copy.read_text(encoding="utf-8")
    marker = "--- issue comment\n"
    assert text.startswith(marker)
    return text[len(marker) :]


# --- lens frontmatter and selection -----------------------------------------


def test_every_real_lens_has_valid_frontmatter():
    """A lens declares only what selection reads; anything else would be a rule nothing enforces."""
    lens_files = sorted((ROOT / "skills" / "next" / "lenses").glob("*.md"))
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
    assert result.stdout.splitlines()[0].startswith("Body: ")
    brief = spilled(result.stdout, "Brief")
    headings = [line for line in brief.splitlines() if line.startswith("### ")]
    assert headings == ["### coverage", "### pen-test", "### principles", "### red-team", "### unknowns"]
    # the lens prose, with its frontmatter and its own title stripped
    assert "Read the artifact as a motivated adversary" in brief
    assert "signals:" not in brief
    assert "# Red team" not in brief
    # named, not printed
    assert "### red-team" not in result.stdout


def test_the_brief_is_written_to_a_file_and_named(fake_gh, tmp_path):
    result = run_deckhand("review", "context", "248")

    assert result.returncode == 0, result.stderr
    assert f"Brief: {tmp_path / 'cache' / 'widgets' / '248-brief.md'}" in result.stdout


def test_context_adds_a_lens_named_in_notes(fake_gh, tmp_path):
    body = ISSUE["body"] + "\n- Lenses: chaos\n"

    result = run_deckhand("review", "context", "248", env=_issue_file(tmp_path, body))

    assert result.returncode == 0, result.stderr
    brief = spilled(result.stdout, "Brief")
    assert "### chaos" in brief.splitlines()
    assert "State the steady state the story assumes" in brief


def test_context_prints_the_three_formats_and_their_paths(fake_gh, tmp_path):
    result = run_deckhand("review", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-8] == (
        'Report findings as {"kind": "findings", "findings": [{"lens": "<lens>", "ordinal": 1, '
        '"severity": "P1|P2|P3", "claim": "<one sentence>", '
        '"evidence": "<one sentence, citing the section>"}]}; an empty list found nothing'
    )
    assert lines[-8] == review.FINDING_FORMAT
    assert lines[-7] == f"Findings: {tmp_path / 'cache' / 'widgets' / '248-findings.json'}"
    assert lines[-6] == (
        'Report verdicts, one per finding in the reviewer\'s order, as {"kind": "verdicts", "verdicts": '
        '[{"id": "<lens>.<n>", "verdict": "CONFIRMED"}, {"id": "<lens>.<n>", "verdict": "REJECTED", '
        '"reason": "<why>"}]}'
    )
    assert lines[-5] == f"Verdicts: {tmp_path / 'cache' / 'widgets' / '248-verdicts.json'}"
    assert lines[-4] == (
        'Report decisions, one per finding, as {"kind": "decisions", "decisions": '
        '[{"id": "<lens>.<n>", "decision": "accepted|declined|changed", "reason": "<why, optional>"}]}'
    )
    assert lines[-4] == review.DECISION_FORMAT
    assert lines[-3] == f"Decisions: {tmp_path / 'cache' / 'widgets' / '248-decisions.json'}"
    assert not (tmp_path / "cache" / "widgets" / "248-findings.json").exists()


def test_context_still_prints_when_the_issue_cannot_be_read(fake_gh, tmp_path):
    result = run_deckhand("review", "context", "248", env=_gh_without_issue_view(tmp_path))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "Body: unavailable (could not find issue 248)"
    brief = spilled(result.stdout, "Brief")
    assert [line for line in brief.splitlines() if line.startswith("### ")] == [
        "### coverage",
        "### principles",
        "### unknowns",
    ]
    assert lines[-8] == review.FINDING_FORMAT
    assert lines[-6] == review.VERDICT_FORMAT
    assert lines[-4] == review.DECISION_FORMAT


def test_context_prints_the_plan_out_of_its_fold(fake_gh, tmp_path):
    """The reviewer reads the plan, and on GitHub the plan sits inside a collapsed block."""
    preamble, parsed = sections.parse(ISSUE["body"])
    folded = sections.render(preamble, parsed)
    assert "<details>" in folded

    result = run_deckhand("review", "context", "248", env=_issue_file(tmp_path, folded))

    assert result.returncode == 0, result.stderr
    body = spilled(result.stdout, "Body")
    assert "<details>" not in body
    assert "</details>" not in body
    assert "### Task 1: Store method" in body


# --- apply ------------------------------------------------------------------


def _findings_doc(*entries) -> str:
    return json.dumps({"kind": "findings", "findings": list(entries)})


def _verdicts_doc(*entries) -> str:
    return json.dumps({"kind": "verdicts", "verdicts": list(entries)})


def _decisions_doc(*entries) -> str:
    return json.dumps({"kind": "decisions", "decisions": list(entries)})


FINDINGS_ENTRIES = [
    {
        "lens": "chaos",
        "ordinal": 1,
        "severity": "P2",
        "claim": "A retried job writes the grant twice",
        "evidence": "Scope In names one write",
    },
    {
        "lens": "unknowns",
        "ordinal": 1,
        "severity": "P3",
        "claim": "The store method is undefined",
        "evidence": "Notes names the file",
    },
]
VERDICTS_ENTRIES = [
    {"id": "chaos.1", "verdict": "CONFIRMED"},
    {"id": "unknowns.1", "verdict": "REJECTED", "reason": "Notes names internal/store/grant.go, which defines it"},
]
DECISIONS_ENTRIES = [
    {"id": "chaos.1", "decision": "accepted"},
    {"id": "unknowns.1", "decision": "declined", "reason": "the skeptic is right"},
]
ONE_ENTRIES = [
    {"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A retry writes twice", "evidence": "Scope In"},
]
FINDINGS = _findings_doc(*FINDINGS_ENTRIES)
VERDICTS = _verdicts_doc(*VERDICTS_ENTRIES)
DECISIONS = _decisions_doc(*DECISIONS_ENTRIES)
ONE = _findings_doc(*ONE_ENTRIES)


def _files(tmp_path: Path, findings: str = FINDINGS, verdicts: str = VERDICTS, decisions: str = DECISIONS) -> list[str]:
    paths = []
    for name, text in (("findings", findings), ("verdicts", verdicts), ("decisions", decisions)):
        path = tmp_path / f"248-{name}.md"
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    return paths


def _apply(
    tmp_path: Path,
    *,
    verdict: str = "Sound; two things to tighten.",
    env: dict[str, str] | None = None,
    **files: str,
):
    findings, verdicts, decisions = _files(tmp_path, **files)
    return run_deckhand("review", "apply", "248", findings, verdicts, decisions, "--verdict", verdict, env=env or {})


def test_apply_posts_the_verdict_and_every_finding_with_its_decision(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "comment.md"

    result = _apply(tmp_path, env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if c.startswith("issue comment")]
    assert call.startswith("issue comment 248 --repo acme/widgets --body-file ")
    assert _posted(copy) == (
        "Review: Sound; two things to tighten.\n"
        "\n"
        "Lenses: coverage (clean), pen-test (clean), principles (clean), red-team (clean), unknowns\n"
        "\n"
        "- chaos.1, P2, accepted: A retried job writes the grant twice. Scope In names one write.\n"
        "- unknowns.1, P3, declined, rejected by the skeptic: The store method is undefined. Notes names the file. "
        "The skeptic is right.\n"
    )
    assert result.stdout.splitlines() == [COMMENT_URL, "Status=Ready"]


def test_apply_moves_the_story_to_ready(fake_gh, gh_calls, tmp_path):
    """A review that has run leaves the story waiting on a kind and points."""
    result = _apply(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "--single-select-option-id opt_ready" in "\n".join(gh_calls())


def test_apply_moves_the_story_to_ready_even_when_the_verdict_asks_for_changes(fake_gh, gh_calls, tmp_path):
    """The verdict is a sentence and is not read; an amend is what moves the story back."""
    result = _apply(tmp_path, verdict="Needs amending: the mechanism is wrong.")

    assert result.returncode == 0, result.stderr
    assert "--single-select-option-id opt_ready" in "\n".join(gh_calls())


def test_a_changed_decision_carries_its_reason(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"

    result = _apply(
        tmp_path,
        decisions=_decisions_doc(
            {"id": "chaos.1", "decision": "changed", "reason": "retry once, not twice"},
            {"id": "unknowns.1", "decision": "declined"},
        ),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert (
        "- chaos.1, P2, changed: A retried job writes the grant twice. Scope In names one write. "
        "Retry once, not twice.\n"
    ) in _posted(copy)


def test_a_line_adds_no_second_stop_to_a_half_that_has_one(fake_gh, tmp_path):
    """A finding is two sentences on one line, so neither half may run into what follows it."""
    copy = tmp_path / "comment.md"
    text = _findings_doc(
        {
            "lens": "chaos",
            "ordinal": 1,
            "severity": "P2",
            "claim": "Does a retry write twice?",
            "evidence": "Scope In names one write.",
        }
    )

    result = _apply(
        tmp_path,
        findings=text,
        verdicts=_verdicts_doc({"id": "chaos.1", "verdict": "CONFIRMED"}),
        decisions=_decisions_doc({"id": "chaos.1", "decision": "accepted", "reason": "once is enough."}),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert "- chaos.1, P2, accepted: Does a retry write twice? Scope In names one write. Once is enough.\n" in (
        _posted(copy)
    )


def test_apply_refuses_a_decision_for_no_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions=_decisions_doc(*DECISIONS_ENTRIES, {"id": "chaos.9", "decision": "accepted"}))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 3: chaos.9 is not a finding\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_finding_without_a_decision(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions=_decisions_doc({"id": "chaos.1", "decision": "accepted"}))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: no decision for unknowns.1\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_two_decisions_for_one_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions=_decisions_doc(*DECISIONS_ENTRIES, {"id": "chaos.1", "decision": "declined"}))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 3: chaos.1 already has a decision\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_decision_that_is_not_one(fake_gh, gh_calls, tmp_path):
    result = _apply(
        tmp_path,
        decisions=_decisions_doc({"id": "chaos.1", "decision": "maybe"}, {"id": "unknowns.1", "decision": "declined"}),
    )

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: entry 1: decision must be one of accepted, declined, changed, got 'maybe'\n"
    )
    assert _writes(gh_calls) == []


def test_an_accepted_decision_on_a_rejected_finding_shows_both(fake_gh, tmp_path):
    """The person may overrule the skeptic; the line then carries the decision and the verdict it overruled."""
    copy = tmp_path / "comment.md"

    result = _apply(
        tmp_path,
        decisions=_decisions_doc(
            {"id": "chaos.1", "decision": "accepted"}, {"id": "unknowns.1", "decision": "accepted"}
        ),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert "- unknowns.1, P3, accepted, rejected by the skeptic: The store method is undefined." in _posted(copy)


def test_a_wrapped_verdict_is_posted_on_one_line(fake_gh, tmp_path):
    """The verdict is the entry's first line, so a newline a shell wrapped in must not reach the log."""
    copy = tmp_path / "comment.md"

    result = _apply(tmp_path, verdict="a\n  b", env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert _posted(copy).startswith(
        "Review: a b\n\nLenses: coverage (clean), pen-test (clean), principles (clean), red-team (clean), "
        "unknowns\n\n- chaos.1"
    )


def test_apply_refuses_a_blank_verdict(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, verdict="  ")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: --verdict needs a sentence on the story as a whole\n"
    assert _writes(gh_calls) == []


def test_a_lens_that_ran_and_found_nothing_is_recorded_as_clean(fake_gh, tmp_path):
    """The lenses selected for issue 248 are coverage, pen-test, principles, red-team, unknowns
    (see test_context_selects_always_and_signal_lenses); pen-test speaks here and the rest stay
    silent, so the posted comment must still name every one of them, the silent ones marked clean.
    """
    copy = tmp_path / "comment.md"
    findings = _findings_doc(
        {
            "lens": "pen-test",
            "ordinal": 1,
            "severity": "P2",
            "claim": "A guest token reaches another org's export",
            "evidence": "Scope In names the filter",
        }
    )
    verdicts = _verdicts_doc({"id": "pen-test.1", "verdict": "CONFIRMED"})
    decisions = _decisions_doc({"id": "pen-test.1", "decision": "accepted"})

    result = _apply(
        tmp_path, findings=findings, verdicts=verdicts, decisions=decisions, env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    posted = _posted(copy)
    assert "Lenses: coverage (clean), pen-test, principles (clean), red-team (clean), unknowns (clean)\n" in posted


def test_a_clean_pass_needs_neither_verdicts_nor_decisions(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    path = tmp_path / "248-findings.md"
    path.write_text(_findings_doc(), encoding="utf-8")

    result = run_deckhand(
        "review", "apply", "248", str(path), "--verdict", "Sound.", env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == (
        "Review: Sound.\n"
        "\n"
        "Lenses: coverage (clean), pen-test (clean), principles (clean), red-team (clean), unknowns (clean)\n"
        "\n"
        "Nothing found.\n"
    )
    assert result.stdout.splitlines() == [COMMENT_URL, "Status=Ready"]


def test_apply_refuses_findings_without_verdicts_or_decisions(fake_gh, gh_calls, tmp_path):
    findings, _, _ = _files(tmp_path)

    result = run_deckhand("review", "apply", "248", findings, "--verdict", "Sound.")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: the findings need the skeptic's verdicts and the person's decisions; pass both files\n"
    )
    assert _writes(gh_calls) == []


def test_apply_refuses_a_stub(fake_gh, gh_calls, tmp_path):
    path = tmp_path / "57-findings.md"
    path.write_text("Nothing found.\n", encoding="utf-8")

    result = run_deckhand("review", "apply", "57", str(path), "--verdict", "Sound.", env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: #57 is a stub; run /deckhand:next 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_reads_past_a_byte_order_mark(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    path = tmp_path / "248-findings.md"
    path.write_bytes(("\ufeff" + _findings_doc()).encode("utf-8"))

    result = run_deckhand(
        "review", "apply", "248", str(path), "--verdict", "Sound.", env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == (
        "Review: Sound.\n"
        "\n"
        "Lenses: coverage (clean), pen-test (clean), principles (clean), red-team (clean), unknowns (clean)\n"
        "\n"
        "Nothing found.\n"
    )


# --- the findings -------------------------------------------------------------


def test_apply_refuses_a_malformed_entry_and_writes_nothing(fake_gh, gh_calls, tmp_path):
    text = json.dumps(
        {
            "kind": "findings",
            "findings": [
                {
                    "lens": "red-team",
                    "ordinal": 1,
                    "severity": "P1",
                    "claim": "A guest reads another collection",
                    "evidence": "Scope In",
                },
                "not a finding",
            ],
        }
    )

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 2: not an object\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_a_duplicate_finding_id(fake_gh, gh_calls, tmp_path):
    text = _findings_doc(
        {"lens": "chaos", "ordinal": 1, "severity": "P1", "claim": "A retry writes twice", "evidence": "Scope In"},
        {"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "The cache outlives the row", "evidence": "Notes"},
    )

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 2: chaos.1 is already the id of an earlier finding\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_an_unknown_lens(fake_gh, gh_calls, tmp_path):
    text = _findings_doc(
        {"lens": "vibes", "ordinal": 1, "severity": "P1", "claim": "It feels wrong", "evidence": "Story"}
    )

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 1: no lens named 'vibes'\n"
    assert _writes(gh_calls) == []


# --- the verdicts -------------------------------------------------------------


def test_apply_refuses_a_verdict_that_is_not_one(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings=ONE, verdicts=_verdicts_doc({"id": "chaos.1", "verdict": "MAYBE"}))

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: entry 1: verdict must be one of CONFIRMED, REJECTED, got 'MAYBE'\n"
    )
    assert _writes(gh_calls) == []


def test_apply_refuses_a_rejection_without_a_reason(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings=ONE, verdicts=_verdicts_doc({"id": "chaos.1", "verdict": "REJECTED"}))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 1: a REJECTED verdict needs a reason\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_verdict_for_no_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(
        tmp_path,
        findings=ONE,
        verdicts=_verdicts_doc({"id": "chaos.1", "verdict": "CONFIRMED"}, {"id": "chaos.2", "verdict": "CONFIRMED"}),
    )

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 2: chaos.2 is not a finding\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_two_verdicts_for_one_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(
        tmp_path,
        findings=ONE,
        verdicts=_verdicts_doc(
            {"id": "chaos.1", "verdict": "CONFIRMED"}, {"id": "chaos.1", "verdict": "REJECTED", "reason": "no"}
        ),
    )

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: entry 2: chaos.1 already has a verdict\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_finding_left_without_a_verdict(fake_gh, gh_calls, tmp_path):
    text = _findings_doc(
        *ONE_ENTRIES, {"lens": "chaos", "ordinal": 2, "severity": "P3", "claim": "Claim", "evidence": "Notes"}
    )

    result = _apply(tmp_path, findings=text, verdicts=_verdicts_doc({"id": "chaos.2", "verdict": "CONFIRMED"}))

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: no verdict for chaos.1\n"
    assert _writes(gh_calls) == []


def test_a_confirmed_verdict_may_carry_a_reason_that_is_not_posted(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"

    result = _apply(
        tmp_path,
        findings=ONE,
        verdicts=_verdicts_doc({"id": "chaos.1", "verdict": "CONFIRMED", "reason": "the retry is real"}),
        decisions=_decisions_doc({"id": "chaos.1", "decision": "accepted"}),
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert "the retry is real" not in _posted(copy)
    assert "rejected" not in _posted(copy)


# --- a file passed where another one belongs -----------------------------------


def test_a_verdicts_file_passed_as_findings_names_the_kind_it_actually_is(fake_gh, tmp_path):
    result = _apply(
        tmp_path,
        findings=_verdicts_doc({"id": "chaos.1", "verdict": "CONFIRMED"}),
        verdicts=_verdicts_doc({"id": "chaos.1", "verdict": "CONFIRMED"}),
        decisions=_decisions_doc({"id": "chaos.1", "decision": "accepted"}),
    )

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: expected a findings file, got verdicts; the order is findings, verdicts, decisions\n"
    )


def test_context_names_the_apply(fake_gh):
    result = run_deckhand("review", "context", "248")

    expected = 'Apply: deckhand review apply 248 <findings> <verdicts> <decisions> --verdict "<sentence>"'
    assert result.stdout.splitlines()[-1] == expected
