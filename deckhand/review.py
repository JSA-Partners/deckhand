"""The review step: one pass over a planned story with every lens that applies, then the skeptic.

`context` prints the story body and a reviewer brief, which is the text of every lens the body
selects, so one reviewer agent carries them all. `apply` reads the skeptic's findings, validates
every line before it writes anything, and posts one comment: the confirmed findings, the rejected
ones, and a Decisions task list the human ticks before `/deckhand:amend`.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from deckhand import gh, issue, sections
from deckhand.step import Refusal, draft_line, read_draft, reason, refuse_stub, step

BRIEF_HEADING = "## Reviewer brief"
CLEAN = "Nothing found."
FINDING_FORMAT = (
    "Report findings as lines: <lens>.<n> | P1|P2|P3 | PENDING | <claim> | "
    f"<evidence, citing the section>; or exactly `{CLEAN}`"
)
SEVERITIES = ("P1", "P2", "P3")
VERDICTS = ("CONFIRMED", "REJECTED")

_EXPECTED = "expected <lens>.<n> | P1|P2|P3 | CONFIRMED|REJECTED | <claim> | <evidence>"
_FRONTMATTER_FIELD = re.compile(r"^([a-z_]+):\s*(.*)$")
# `Lenses: a, b` anywhere in Notes, as a line of its own or as a bullet.
_NAMED_LENSES = re.compile(r"^[\s-]*Lenses:\s*(.+)$", re.MULTILINE)
_TRUE_WORDS = ("yes", "true", "on", "1")


def lenses_dir() -> Path:
    """The directory holding the lens files: DECKHAND_LENSES, else <plugin root>/skills/review/lenses."""
    override = os.environ.get("DECKHAND_LENSES")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "skills" / "review" / "lenses"


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
    """`(the block to print, the text to select lenses from)`; the text is empty when the read fails."""
    try:
        body = issue.view(gh.repo_slug(), number).body
    except Exception as error:  # the brief is still worth printing without the story
        return f"Body: unavailable ({reason(error)})", ""
    return body.strip("\n"), body


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
    return 0


# --- apply ------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One reviewed finding: the skeptic's line, split into its columns."""

    lens: str
    ordinal: int
    severity: str
    verdict: str
    claim: str
    evidence: str

    @property
    def id(self) -> str:
        return f"{self.lens}.{self.ordinal}"

    @property
    def order(self) -> tuple[str, str, int]:
        return self.severity, self.lens, self.ordinal


def _parse(line: str) -> Finding | None:
    """One findings line as a `Finding`, or None when it is not in the format the brief stated."""
    parts = [part.strip() for part in line.split("|")]
    if len(parts) != 5:
        return None
    ref, severity, verdict, claim, evidence = parts
    lens, dot, ordinal = ref.partition(".")
    if not lens or not dot or not (ordinal.isascii() and ordinal.isdigit()):
        return None
    if severity not in SEVERITIES or verdict not in VERDICTS or not claim or not evidence:
        return None
    return Finding(lens, int(ordinal), severity, verdict, claim, evidence)


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


def _claim(finding: Finding) -> str:
    """The claim ended with a stop, so the evidence reads as the sentence after it."""
    claim = finding.claim.rstrip()
    return claim if claim.endswith((".", "!", "?", ":")) else claim + "."


def comment_body(confirmed: list[Finding], rejected: list[Finding]) -> str:
    """The review comment: the confirmed findings, the rejected ones, and the Decisions task list."""
    if not confirmed and not rejected:
        return f"{issue.REVIEW_HEADING}\n\n{CLEAN}\n"
    blocks = [issue.REVIEW_HEADING]
    for finding in confirmed:
        blocks.append(f"**{finding.id}, {finding.severity}, CONFIRMED** {_claim(finding)} {finding.evidence}")
    if rejected:
        blocks.append("\n".join(f"Rejected: {finding.id} ({finding.evidence})" for finding in rejected))
    if confirmed:
        blocks.append("### Decisions")
        blocks.append("\n".join(f"- [ ] {finding.id}" for finding in confirmed))
    return "\n\n".join(blocks) + "\n"


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, help="the reviewed findings")


@step("review", _configure)
def apply(args: argparse.Namespace) -> int:
    """Post the reviewed findings on the issue as one comment."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    found = findings(read_draft(args.file))
    confirmed = sorted((f for f in found if f.verdict == "CONFIRMED"), key=lambda f: f.order)
    rejected = [f for f in found if f.verdict == "REJECTED"]
    url = issue.comment(repo, args.issue, comment_body(confirmed, rejected))
    print(url)
    if confirmed:
        print(
            f"Next: tick the findings to accept, run /deckhand:amend {args.issue}, "
            f"then reply Approved and /deckhand:ready {args.issue}"
        )
    else:
        print(f"Next: reply Approved on the issue, then /deckhand:ready {args.issue}")
    return 0
