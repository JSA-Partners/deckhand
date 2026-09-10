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
    return sorted((ROOT / "skills" / "next" / "lenses").glob("*.md"))


def test_context_adds_a_lens_named_in_notes(fake_gh, tmp_path):
    body = ISSUE["body"] + "\n- Lenses: chaos\n"

    result = run_deckhand("review", "context", "248", env=_issue_file(tmp_path, body))

    assert result.returncode == 0, result.stderr
    assert "### chaos" in result.stdout.splitlines()
    assert "State the steady state the story assumes" in result.stdout


def test_context_prints_the_three_formats_and_their_paths(fake_gh, tmp_path):
    result = run_deckhand("review", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-6] == (
        "Report findings as lines: <lens>.<n> | P1|P2|P3 | PENDING | <claim> | "
        "<evidence, citing the section>; or exactly `Nothing found.`"
    )
    assert lines[-6] == review.FINDING_FORMAT
    assert lines[-5] == f"Findings: {tmp_path / 'cache' / 'widgets' / '248-findings.md'}"
    assert lines[-4] == (
        "Report verdicts as lines, one per finding in the reviewer's order: <lens>.<n> | CONFIRMED, "
        "or <lens>.<n> | REJECTED | <reason>"
    )
    assert lines[-3] == f"Verdicts: {tmp_path / 'cache' / 'widgets' / '248-verdicts.md'}"
    assert lines[-2] == (
        "Report decisions as lines, one per finding: <lens>.<n> | accepted|declined|changed [| <reason>]"
    )
    assert lines[-2] == review.DECISION_FORMAT
    assert lines[-1] == f"Decisions: {tmp_path / 'cache' / 'widgets' / '248-decisions.md'}"
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
    assert lines[-6] == review.FINDING_FORMAT
    assert lines[-4] == review.VERDICT_FORMAT
    assert lines[-2] == review.DECISION_FORMAT


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


FINDINGS = (
    "chaos.1 | P2 | PENDING | A retried job writes the grant twice | Scope In names one write\n"
    "unknowns.1 | P3 | PENDING | The store method is undefined | Notes names the file\n"
)
VERDICTS = "chaos.1 | CONFIRMED\nunknowns.1 | REJECTED | Notes names internal/store/grant.go, which defines it\n"
DECISIONS = "chaos.1 | accepted\nunknowns.1 | declined | the skeptic is right\n"
ONE = "chaos.1 | P2 | PENDING | A retry writes twice | Scope In\n"


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
        "- chaos.1, P2, accepted: A retried job writes the grant twice. Scope In names one write.\n"
        "- unknowns.1, P3, declined, rejected by the skeptic: The store method is undefined. Notes names the file. "
        "Because the skeptic is right.\n"
    )
    assert result.stdout.splitlines() == [COMMENT_URL]


def test_a_changed_decision_carries_its_reason(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"

    result = _apply(
        tmp_path,
        decisions="chaos.1 | changed | retry once, not twice\nunknowns.1 | declined\n",
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert (
        "- chaos.1, P2, changed: A retried job writes the grant twice. Scope In names one write. "
        "Because retry once, not twice.\n"
    ) in _posted(copy)


def test_a_line_adds_no_second_stop_to_a_half_that_has_one(fake_gh, tmp_path):
    """A finding is two sentences on one line, so neither half may run into what follows it."""
    copy = tmp_path / "comment.md"
    text = "chaos.1 | P2 | PENDING | Does a retry write twice? | Scope In names one write.\n"

    result = _apply(
        tmp_path,
        findings=text,
        verdicts="chaos.1 | CONFIRMED\n",
        decisions="chaos.1 | accepted | once is enough.\n",
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert "- chaos.1, P2, accepted: Does a retry write twice? Scope In names one write. Because once is enough.\n" in (
        _posted(copy)
    )


def test_apply_refuses_a_decision_for_no_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions=DECISIONS + "chaos.9 | accepted\n")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 3: chaos.9 is not a finding\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_finding_without_a_decision(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions="chaos.1 | accepted\n")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: no decision for unknowns.1\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_two_decisions_for_one_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions=DECISIONS + "chaos.1 | declined\n")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 3: chaos.1 already has a decision\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_decision_that_is_not_one(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions="chaos.1 | maybe\nunknowns.1 | declined\n")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: line 1: expected <lens>.<n> | accepted|declined|changed [| <reason>]\n"
    )
    assert _writes(gh_calls) == []


def test_apply_refuses_a_decision_with_a_pipe_in_its_reason(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, decisions="chaos.1 | accepted | a | b\nunknowns.1 | declined\n")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: line 1: expected <lens>.<n> | accepted|declined|changed [| <reason>]\n"
    )
    assert _writes(gh_calls) == []


def test_an_accepted_decision_on_a_rejected_finding_shows_both(fake_gh, tmp_path):
    """The person may overrule the skeptic; the line then carries the decision and the verdict it overruled."""
    copy = tmp_path / "comment.md"

    result = _apply(
        tmp_path, decisions="chaos.1 | accepted\nunknowns.1 | accepted\n", env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert "- unknowns.1, P3, accepted, rejected by the skeptic: The store method is undefined." in _posted(copy)


def test_a_wrapped_verdict_is_posted_on_one_line(fake_gh, tmp_path):
    """The verdict is the entry's first line, so a newline a shell wrapped in must not reach the log."""
    copy = tmp_path / "comment.md"

    result = _apply(tmp_path, verdict="a\n  b", env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert _posted(copy).startswith("Review: a b\n\n- chaos.1")


def test_apply_refuses_a_blank_verdict(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, verdict="  ")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: --verdict needs a sentence on the story as a whole\n"
    assert _writes(gh_calls) == []


def test_a_clean_pass_needs_neither_verdicts_nor_decisions(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    path = tmp_path / "248-findings.md"
    path.write_text("Nothing found.\n", encoding="utf-8")

    result = run_deckhand(
        "review", "apply", "248", str(path), "--verdict", "Sound.", env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == "Review: Sound.\n\nNothing found.\n"
    assert result.stdout.splitlines() == [COMMENT_URL]


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
    assert result.stderr == "deckhand review apply: #57 is a stub; run /deckhand:new 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_reads_past_a_byte_order_mark(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"
    path = tmp_path / "248-findings.md"
    path.write_bytes("\ufeffNothing found.\n".encode("utf-8"))

    result = run_deckhand(
        "review", "apply", "248", str(path), "--verdict", "Sound.", env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == "Review: Sound.\n\nNothing found.\n"


# --- the findings -------------------------------------------------------------


def test_apply_refuses_a_malformed_line_and_writes_nothing(fake_gh, gh_calls, tmp_path):
    text = "red-team.1 | P1 | PENDING | A guest reads another collection | Scope In\nnot a finding\n"

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: line 2: expected <lens>.<n> | P1|P2|P3 | PENDING | <claim> | <evidence>\n"
    )
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_a_pipe_inside_a_claim(fake_gh, gh_calls, tmp_path):
    text = "chaos.1 | P1 | PENDING | foo(a | b) is never bounded | Scope In\n"

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand review apply: line 1: expected ")
    assert _writes(gh_calls) == []


def test_apply_refuses_a_duplicate_finding_id(fake_gh, gh_calls, tmp_path):
    text = (
        "chaos.1 | P1 | PENDING | A retry writes twice | Scope In\n"
        "chaos.1 | P2 | PENDING | The cache outlives the row | Notes\n"
    )

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 2: chaos.1 is already the id of an earlier finding\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_an_unknown_lens(fake_gh, gh_calls, tmp_path):
    text = "vibes.1 | P1 | PENDING | It feels wrong | Story\n"

    result = _apply(tmp_path, findings=text)

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 1: no lens named 'vibes'\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_an_empty_findings_file(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings="\n\n")

    assert result.returncode == 1
    assert "the findings file is empty" in result.stderr
    assert _writes(gh_calls) == []


# --- the verdicts -------------------------------------------------------------


def test_apply_refuses_a_verdict_that_is_not_one(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings=ONE, verdicts="chaos.1 | MAYBE\n")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand review apply: line 1: expected <lens>.<n> | CONFIRMED, or <lens>.<n> | REJECTED | <reason>\n"
    )
    assert _writes(gh_calls) == []


def test_apply_refuses_a_rejection_without_a_reason(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings=ONE, verdicts="chaos.1 | REJECTED\n")

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand review apply: line 1: expected ")
    assert _writes(gh_calls) == []


def test_apply_refuses_a_verdict_for_no_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings=ONE, verdicts="chaos.1 | CONFIRMED\nchaos.2 | CONFIRMED\n")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 2: chaos.2 is not a finding\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_two_verdicts_for_one_finding(fake_gh, gh_calls, tmp_path):
    result = _apply(tmp_path, findings=ONE, verdicts="chaos.1 | CONFIRMED\nchaos.1 | REJECTED | no\n")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: line 2: chaos.1 already has a verdict\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_finding_left_without_a_verdict(fake_gh, gh_calls, tmp_path):
    text = ONE + "chaos.2 | P3 | PENDING | Claim | Notes\n"

    result = _apply(tmp_path, findings=text, verdicts="chaos.2 | CONFIRMED\n")

    assert result.returncode == 1
    assert result.stderr == "deckhand review apply: no verdict for chaos.1\n"
    assert _writes(gh_calls) == []


def test_a_confirmed_verdict_may_carry_a_reason_that_is_not_posted(fake_gh, tmp_path):
    copy = tmp_path / "comment.md"

    result = _apply(
        tmp_path,
        findings=ONE,
        verdicts="chaos.1 | CONFIRMED | the retry is real\n",
        decisions="chaos.1 | accepted\n",
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert "the retry is real" not in _posted(copy)
    assert "rejected" not in _posted(copy)
