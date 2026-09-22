"""The review step: one pass over a planned story with every lens that applies, then the skeptic.

`context` writes the story body to a file and names it, then prints a reviewer brief, which is the
text of every lens the body selects, so one reviewer agent carries them all. `apply` reads the
reviewer's findings, the skeptic's verdicts, and the decisions the person made in the session as the
three files the session wrote, joins them itself, validates every line before it writes anything,
and posts one `Review:` log entry: the verdict on the story as a whole, which lenses the same
selection ran with the silent ones marked clean, then every finding on a line with the person's
decision beside it and the skeptic's rejection marked. Neither agent's lines are retyped on the way,
so a format only one of them keeps cannot cost a finding.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterable
from pathlib import Path

from deckhand import findings as findings_file
from deckhand import gh, invoke, issue, sections
from deckhand.step import PLUGIN_ROOT, Refusal, draft_line, read_draft, refuse_stub, spill, step, usable

BRIEF_HEADING = "## Reviewer brief"
FINDING_FORMAT = (
    "Report findings as lines: <lens>.<n> | P1|P2|P3 | PENDING | <claim> | "
    f"<evidence, citing the section>; or exactly `{findings_file.CLEAN}`"
)
VERDICT_FORMAT = (
    "Report verdicts as lines, one per finding in the reviewer's order: <lens>.<n> | CONFIRMED, "
    "or <lens>.<n> | REJECTED | <reason>"
)
DECISION_FORMAT = "Report decisions as lines, one per finding: <lens>.<n> | accepted|declined|changed [| <reason>]"

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


def context(args: argparse.Namespace) -> int:
    """Print where the story body was written, the text of every lens that applies, and where the findings go."""
    story: issue.Issue | Exception
    try:
        story = issue.view(gh.repo_slug(), args.issue)
    except Exception as error:  # the brief is still worth printing without the story
        story = error
    text = "" if isinstance(story, Exception) else story.body
    print(spill("Body", f"{args.issue}-issue.md", lambda: sections.bare(usable(story).body)))
    lenses = _lens_files()
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
    print()
    print(
        invoke.apply_line(
            "review", str(args.issue), "<findings>", "<verdicts>", "<decisions>", '--verdict "<sentence>"'
        )
    )
    return 0


# --- apply ------------------------------------------------------------------


def _stop(text: str) -> str:
    """`text` ended with a stop, so the claim and the evidence each read as a sentence of their own."""
    stripped = text.rstrip()
    return stripped if stripped.endswith((".", "!", "?", ":")) else stripped + "."


def _line(finding: findings_file.Finding, decision: str, why: str) -> str:
    """One finding as the log reads it: id, severity, the decision, the skeptic's verdict, then the text."""
    verdict = ", rejected by the skeptic" if finding.verdict == "REJECTED" else ""
    reason_text = f" {_stop(why[0].upper() + why[1:])}" if why else ""
    text = f"{_stop(finding.claim)} {_stop(finding.evidence)}{reason_text}"
    return f"- {finding.id}, {finding.severity}, {decision}{verdict}: {text}"


def _lens_line(ran: list[str], found: list[findings_file.Finding]) -> str:
    """Which lenses ran, and which had nothing to say; absence is the record, so it is written down."""
    spoke = {finding.lens for finding in found}
    named = [name if name in spoke else f"{name} (clean)" for name in ran]
    return f"Lenses: {', '.join(named)}"


def comment_body(
    verdict: str, ran: list[str], found: list[findings_file.Finding], decided: dict[str, tuple[str, str]]
) -> str:
    """The `Review:` entry: the verdict, which lenses ran, then every finding with its decision beside it."""
    parts = [f"Review: {verdict}"]
    if ran:
        parts.append(_lens_line(ran, found))
    parts.append(findings_file.CLEAN if not found else "\n".join(_line(f, *decided[f.id]) for f in found))
    return "\n\n".join(parts) + "\n"


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
    lenses = _lens_files()
    ran = _selected(lenses, story.body, named_lenses(story.body))
    found = findings_file.findings(read_draft(args.findings), set(lenses))
    decided: dict[str, tuple[str, str]] = {}
    if found:
        if args.verdicts is None or args.decisions is None:
            raise Refusal("the findings need the skeptic's verdicts and the person's decisions; pass both files")
        found = findings_file.judged(found, read_draft(args.verdicts))
        decided = findings_file.decisions(read_draft(args.decisions), found)
    print(issue.comment(repo, args.issue, comment_body(verdict, ran, found, decided)))
    return 0
