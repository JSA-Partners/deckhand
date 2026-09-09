"""The review step: one pass over a planned story with every lens that applies, then the skeptic.

`context` prints the story body and a reviewer brief, which is the text of every lens the body
selects, so one reviewer agent carries them all. `apply` reads the skeptic's findings, validates
every line before it writes anything, and posts one comment: what the reader does with it, then each
finding on a line the human ticks before the amend step, the skeptic's rejections marked as such.

A review is read in one sitting, so only the `CAP` most severe confirmed findings are posted and the
rest are counted in a closing line; a rejection is the skeptic's own and is always shown.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from deckhand import gh, issue, sections
from deckhand.step import PLUGIN_ROOT, Refusal, draft_line, read_draft, reason, refuse_stub, step

BRIEF_HEADING = "## Reviewer brief"
CLEAN = "Nothing found."
FINDING_FORMAT = (
    "Report findings as lines: <lens>.<n> | P1|P2|P3 | PENDING | <claim> | "
    f"<evidence, citing the section>; or exactly `{CLEAN}`"
)
CAP = 7
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
    return PLUGIN_ROOT / "skills" / "review" / "lenses"


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


def _stop(text: str) -> str:
    """`text` ended with a stop, so the claim and the evidence each read as a sentence of their own."""
    stripped = text.rstrip()
    return stripped if stripped.endswith((".", "!", "?", ":")) else stripped + "."


def guidance(number: int) -> str:
    """What the person reading the issue does: what a tick means, and which command follows."""
    return (
        "Tick a finding to accept it; leave it unticked to decline. A finding marked rejected by the "
        "skeptic is shown for the record; tick it only to overrule them. Replies here are read the "
        f"next time the story is amended. Then run `/deckhand:next {number}`: it applies what you "
        "ticked and replied, or boards the story when you accepted nothing."
    )


def next_line(number: int) -> str:
    """The line the step ends on: the decision is the person's, on the issue, and then one command.

    A clean review ends on it too. There is nothing to tick, but a reply is still read, and the
    person types the same thing either way rather than remembering which review they had.
    """
    return f"Next: /deckhand:next {number} when you have ticked and replied."


def _line(finding: Finding, rejected: bool = False) -> str:
    """One finding as the box the reader ticks, its id and severity bold and the skeptic's verdict in."""
    verdict = ", rejected by the skeptic" if rejected else ""
    return f"- [ ] **{finding.id}, {finding.severity}{verdict}** {_stop(finding.claim)} {_stop(finding.evidence)}"


def _withheld_line(number: int, withheld: int) -> str:
    """The closing count: what was found and not posted, and the choice that asks for it again."""
    findings_word = "finding" if withheld == 1 else "findings"
    return (
        f"{withheld} further {findings_word} withheld; after amending, choose Review it again when "
        f"/deckhand:next {number} offers it."
    )


def comment_body(number: int, confirmed: list[Finding], rejected: list[Finding], withheld: int = 0) -> str:
    """The review comment: what to do with it, then one tickable line per finding.

    The comment is all most readers ever see of a review, so the decision sits on the finding it
    belongs to, the rejected ones included, and the guidance sits above them all. `withheld` is the
    count of confirmed findings the cap left off, so the reader knows the list is not the whole of it.
    """
    if not confirmed and not rejected and not withheld:
        clean = f"Reply here with anything still wrong, then run `/deckhand:next {number}`."
        return f"{issue.REVIEW_HEADING}\n\n{CLEAN}\n\n{clean}\n"
    lines = [_line(finding) for finding in confirmed] + [_line(finding, True) for finding in rejected]
    parts = [issue.REVIEW_HEADING, guidance(number)]
    if lines:
        parts.append("\n".join(lines))
    if withheld > 0:
        parts.append(_withheld_line(number, withheld))
    return "\n\n".join(parts) + "\n"


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
    rejected = sorted((f for f in found if f.verdict == "REJECTED"), key=lambda f: f.order)
    posted, rest = confirmed[:CAP], confirmed[CAP:]
    url = issue.comment(repo, args.issue, comment_body(args.issue, posted, rejected, len(rest)))
    print(url)
    print(next_line(args.issue))
    return 0
