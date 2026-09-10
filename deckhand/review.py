"""The review step: one pass over a planned story with every lens that applies, then the skeptic.

`context` prints the story body and a reviewer brief, which is the text of every lens the body
selects, so one reviewer agent carries them all. `apply` reads the reviewer's findings, the
skeptic's verdicts, and the decisions the person made in the session as the three files the
session wrote, joins them itself, validates every line before it writes anything, and posts one
`Review:` log entry: the verdict on the story as a whole, then every finding on a line with the
person's decision beside it and the skeptic's rejection marked. Neither agent's lines are retyped
on the way, so a format only one of them keeps cannot cost a finding.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from deckhand import gh, issue, sections
from deckhand.step import PLUGIN_ROOT, Refusal, draft_line, read_draft, reason, refuse_stub, step

BRIEF_HEADING = "## Reviewer brief"
CLEAN = "Nothing found."
FINDING_FORMAT = (
    "Report findings as lines: <lens>.<n> | P1|P2|P3 | PENDING | <claim> | "
    f"<evidence, citing the section>; or exactly `{CLEAN}`"
)
VERDICT_FORMAT = (
    "Report verdicts as lines, one per finding in the reviewer's order: <lens>.<n> | CONFIRMED, "
    "or <lens>.<n> | REJECTED | <reason>"
)
DECISION_FORMAT = "Report decisions as lines, one per finding: <lens>.<n> | accepted|declined|changed [| <reason>]"
SEVERITIES = ("P1", "P2", "P3")
PENDING = "PENDING"
VERDICTS = ("CONFIRMED", "REJECTED")
DECISIONS = ("accepted", "declined", "changed")

_EXPECTED = f"expected <lens>.<n> | P1|P2|P3 | {PENDING} | <claim> | <evidence>"
_EXPECTED_VERDICT = "expected <lens>.<n> | CONFIRMED, or <lens>.<n> | REJECTED | <reason>"
_EXPECTED_DECISION = "expected <lens>.<n> | accepted|declined|changed [| <reason>]"
_FRONTMATTER_FIELD = re.compile(r"^([a-z_]+):\s*(.*)$")
# `Lenses: a, b` anywhere in Notes, as a line of its own or as a bullet.
_NAMED_LENSES = re.compile(r"^[\s-]*Lenses:\s*(.+)$", re.MULTILINE)
_TRUE_WORDS = ("yes", "true", "on", "1")


def lenses_dir() -> Path:
    """The directory holding the lens files: DECKHAND_LENSES, else <plugin root>/skills/next/lenses."""
    override = os.environ.get("DECKHAND_LENSES")
    if override:
        return Path(override)
    return PLUGIN_ROOT / "skills" / "next" / "lenses"


def _lens_files() -> dict[str, str]:
    """Every lens by name, with its file's text, read once so one pass never opens a lens twice."""
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(lenses_dir().glob("*.md"))}


def _scalar(raw: str) -> str:
    """A frontmatter value without its matching surrounding quotes or a trailing ` # comment`."""
    value = raw.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[0] in value[1:]:
        return value[1 : value.index(value[0], 1)]
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _frontmatter(text: str) -> dict[str, str]:
    """The `key: value` pairs between the first two `---` lines; empty when the file has none."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        match = _FRONTMATTER_FIELD.match(line)
        if match:
            fields[match.group(1)] = _scalar(match.group(2))
    return fields


def _body_text(text: str) -> str:
    """A lens file's prose: its frontmatter gone, and its own title gone so the brief names it once."""
    lines = text.splitlines()
    if lines and lines[0] == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                lines = lines[index + 1 :]
                break
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip("\n")


def _signals(raw: str) -> list[str]:
    """`[a, b, c]` -> `["a", "b", "c"]`."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [s.strip() for s in raw.split(",") if s.strip()]


def _matches(signal: str, text_lower: str) -> bool:
    """Whole-word, case-insensitive containment (the text is already lowercased)."""
    return re.search(rf"\b{re.escape(signal.lower())}\b", text_lower) is not None


def _applies(fields: dict[str, str], text_lower: str) -> bool:
    """Whether one lens's frontmatter reaches into `text_lower`: it is always on, or a signal matches."""
    if fields.get("always", "").strip().lower() in _TRUE_WORDS:
        return True
    return any(_matches(signal, text_lower) for signal in _signals(fields.get("signals", "")))


def _selected(lenses: dict[str, str], text: str, named: Iterable[str] = ()) -> list[str]:
    """The names in `lenses` to run over `text`, in name order."""
    lowered = text.lower()
    wanted = {name.strip() for name in named if name.strip()}
    return [name for name, raw in lenses.items() if name in wanted or _applies(_frontmatter(raw), lowered)]


def named_lenses(body: str) -> list[str]:
    """The lenses a `Lenses: a, b` line in Notes asks for; empty when the body names none.

    Only the first word of an entry is the name, so `chaos (retry path)` still selects chaos.
    """
    match = _NAMED_LENSES.search(sections.get(body, "Notes", ""))
    if match is None:
        return []
    return [entry.split()[0] for entry in match.group(1).split(",") if entry.split()]


# --- context ----------------------------------------------------------------


def _body(number: int) -> tuple[str, str]:
    """`(the block to print, the text to select lenses from)`; the text is empty when the read fails.

    The plan sits in a collapsed block on GitHub; the reviewer is handed it bare, as the story reads.
    """
    try:
        body = issue.view(gh.repo_slug(), number).body
    except Exception as error:  # the brief is still worth printing without the story
        return f"Body: unavailable ({reason(error)})", ""
    return sections.bare(body).strip("\n"), body


def context(args: argparse.Namespace) -> int:
    """Print the story, the text of every lens that applies to it, and where the findings go."""
    block, text = _body(args.issue)
    lenses = _lens_files()
    print(block)
    print()
    print(BRIEF_HEADING)
    for name in _selected(lenses, text, named_lenses(text)):
        print()
        print(f"### {name}")
        print()
        print(_body_text(lenses[name]))
    print()
    print(FINDING_FORMAT)
    print(draft_line("Findings", f"{args.issue}-findings.md"))
    print(VERDICT_FORMAT)
    print(draft_line("Verdicts", f"{args.issue}-verdicts.md"))
    print(DECISION_FORMAT)
    print(draft_line("Decisions", f"{args.issue}-decisions.md"))
    return 0


# --- apply ------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One finding: the reviewer's line split into its columns, with the skeptic's verdict on it."""

    lens: str
    ordinal: int
    severity: str
    verdict: str
    claim: str
    evidence: str
    rejection: str = ""

    @property
    def id(self) -> str:
        return f"{self.lens}.{self.ordinal}"


def _id(ref: str) -> tuple[str, int] | None:
    """`(lens, ordinal)` from `<lens>.<n>`, or None when it is not one."""
    lens, dot, ordinal = ref.partition(".")
    if not lens or not dot or not (ordinal.isascii() and ordinal.isdigit()):
        return None
    return lens, int(ordinal)


def _parse(line: str) -> Finding | None:
    """One findings line as a `Finding`, or None when it is not in the format the brief stated."""
    parts = [part.strip() for part in line.split("|")]
    if len(parts) != 5:
        return None
    ref, severity, verdict, claim, evidence = parts
    found = _id(ref)
    if found is None or severity not in SEVERITIES or verdict != PENDING or not claim or not evidence:
        return None
    return Finding(*found, severity, verdict, claim, evidence)


def _parse_verdict(line: str) -> tuple[str, str, str] | None:
    """`(id, verdict, reason)` from one verdict line, or None; a rejection has to say why."""
    parts = [part.strip() for part in line.split("|")]
    if len(parts) not in (2, 3) or _id(parts[0]) is None or parts[1] not in VERDICTS:
        return None
    reason_text = parts[2] if len(parts) == 3 else ""
    if parts[1] == "REJECTED" and not reason_text:
        return None
    return parts[0], parts[1], reason_text if parts[1] == "REJECTED" else ""


def _parse_decision(line: str) -> tuple[str, str, str] | None:
    """`(id, decision, reason)` from one decision line, or None; the reason is the person's and optional."""
    parts = [part.strip() for part in line.split("|")]
    if len(parts) not in (2, 3) or _id(parts[0]) is None or parts[1] not in DECISIONS:
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""


def _by_finding(
    text: str, found: list[Finding], parse: Callable[[str], tuple[str, str, str] | None], noun: str, expected: str
) -> dict[str, tuple[str, str]]:
    """One `(word, reason)` per finding id from `text`, read with `parse`; every finding gets one, and only findings do.

    The verdicts and the decisions are the same shape read from two files, so one reader holds the
    rules and each caller names only its parse and its noun for the refusals.
    """
    ids = {finding.id for finding in found}
    read: dict[str, tuple[str, str]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parsed = parse(line)
        if parsed is None:
            raise Refusal(f"line {number}: {expected}")
        ref, word, why = parsed
        if ref not in ids:
            raise Refusal(f"line {number}: {ref} is not a finding")
        if ref in read:
            raise Refusal(f"line {number}: {ref} already has a {noun}")
        read[ref] = (word, why)
    missing = [finding.id for finding in found if finding.id not in read]
    if missing:
        raise Refusal(f"no {noun} for {', '.join(missing)}")
    return read


def verdicts(text: str, found: list[Finding]) -> dict[str, tuple[str, str]]:
    """The skeptic's verdict and reason by finding id."""
    return _by_finding(text, found, _parse_verdict, "verdict", _EXPECTED_VERDICT)


def decisions(text: str, found: list[Finding]) -> dict[str, tuple[str, str]]:
    """The person's decision and reason by finding id."""
    return _by_finding(text, found, _parse_decision, "decision", _EXPECTED_DECISION)


def judged(found: list[Finding], text: str) -> list[Finding]:
    """The findings with the skeptic's verdicts on them, in the reviewer's order."""
    by_id = verdicts(text, found)
    return [replace(f, verdict=by_id[f.id][0], rejection=by_id[f.id][1]) for f in found]


def findings(text: str) -> list[Finding]:
    """Every finding in `text`; a line that is not in the stated format is a refusal, not a guess."""
    if text.strip() == CLEAN:
        return []
    known = {path.stem for path in lenses_dir().glob("*.md")}
    parsed: list[Finding] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        found = _parse(line)
        if found is None:
            raise Refusal(f"line {number}: {_EXPECTED}")
        if found.lens not in known:
            raise Refusal(f"line {number}: no lens named '{found.lens}'")
        if found.id in seen:
            raise Refusal(f"line {number}: {found.id} is already the id of an earlier finding")
        seen.add(found.id)
        parsed.append(found)
    if not parsed:
        raise Refusal(f"the findings file is empty; write the findings, or exactly `{CLEAN}`")
    return parsed


def _stop(text: str) -> str:
    """`text` ended with a stop, so the claim and the evidence each read as a sentence of their own."""
    stripped = text.rstrip()
    return stripped if stripped.endswith((".", "!", "?", ":")) else stripped + "."


def _line(finding: Finding, decision: str, why: str) -> str:
    """One finding as the log reads it: id, severity, the decision, the skeptic's verdict, then the text."""
    verdict = ", rejected by the skeptic" if finding.verdict == "REJECTED" else ""
    because = f" Because {_stop(why)}" if why else ""
    text = f"{_stop(finding.claim)} {_stop(finding.evidence)}{because}"
    return f"- {finding.id}, {finding.severity}, {decision}{verdict}: {text}"


def comment_body(verdict: str, found: list[Finding], decided: dict[str, tuple[str, str]]) -> str:
    """The `Review:` entry: the verdict, then every finding with the person's decision beside it."""
    head = f"Review: {verdict}"
    if not found:
        return f"{head}\n\n{CLEAN}\n"
    lines = [_line(f, *decided[f.id]) for f in found]
    return head + "\n\n" + "\n".join(lines) + "\n"


def _verdict(value: str) -> str:
    """The sentence on the story as a whole, its whitespace collapsed to single spaces.

    It sits on the log entry's first line, which the log reader takes as the whole entry, so an
    argument a shell wrapped onto two lines must not carry its newline onto the issue.
    """
    said = " ".join(value.split())
    if not said:
        raise Refusal("--verdict needs a sentence on the story as a whole")
    return said


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("findings", type=Path, help="the reviewer's findings")
    parser.add_argument("verdicts", type=Path, nargs="?", help="the skeptic's verdicts; not needed for a clean pass")
    parser.add_argument("decisions", type=Path, nargs="?", help="the person's decisions; not needed for a clean pass")
    parser.add_argument("--verdict", required=True, help="one sentence on the story as a whole")


@step("review", _configure)
def apply(args: argparse.Namespace) -> int:
    """Post the review on the issue as one log entry: the verdict and every finding with its decision."""
    verdict = _verdict(args.verdict)
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    found = findings(read_draft(args.findings))
    decided: dict[str, tuple[str, str]] = {}
    if found:
        if args.verdicts is None or args.decisions is None:
            raise Refusal("the findings need the skeptic's verdicts and the person's decisions; pass both files")
        found = judged(found, read_draft(args.verdicts))
        decided = decisions(read_draft(args.decisions), found)
    print(issue.comment(repo, args.issue, comment_body(verdict, found, decided)))
    return 0
