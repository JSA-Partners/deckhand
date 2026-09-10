"""Every skill file is a contract with Claude Code: this is the part a test can hold it to.

One injection line per skill, naming that skill's own step, is the whole convention: the context a
step needs is computed, never gathered by instruction, and a skill that names a command the CLI no
longer has fails the run rather than the user's session.
"""

from __future__ import annotations

import re

import pytest

from deckhand import cli
from tests.conftest import ROOT

SKILLS = sorted(path for path in (ROOT / "skills").iterdir() if (path / "SKILL.md").is_file())
AGENTS = sorted((ROOT / "agents").glob("*.md"))

# The one command each skill injects, by skill name.
INJECTS = {
    "commit": "commit context",
    "document": "document context",
    "new": "new context",
    "next": "next context",
    "setup": "setup context",
}

# The skills a person types and the model never picks: each one writes to GitHub, so its timing
# belongs to the person.
TYPED = {"setup", "new", "next"}

# The steps `next` carries. They were skills once; nothing a person reads may still name one as a
# command to type.
STEPS = ("amend", "finish", "ready", "review", "start")

# The word limit is one per skill file: `next` carries what five skills carried, and `new` runs a
# whole brainstorm; everything else stays short.
WORD_LIMITS = {"next": 1100, "new": 300}
DEFAULT_WORD_LIMIT = 200

GRANT = 'Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *)'
BARE_GRANT = "Bash(deckhand *)"
RECOMMEND = re.compile(r"\brecommend", re.IGNORECASE)

# Names of the dispatcher, the recipe files, the commands that went with them, and the plan step
# amend absorbed. Most are distinctive enough to match anywhere; Actions, workflow, dispatch, and
# plan are ordinary words, so they match whole, for Actions only as GitHub spells it, and for
# dispatch and plan only where they named the retired command rather than ordinary prose.
RETIRED = [
    re.compile(pattern, flags)
    for pattern, flags in (
        (r"\bdispatcher\b", re.IGNORECASE),
        (r"deckhand[\"']?\s+dispatch\b", re.IGNORECASE),
        (r"deckhand[\"']?\s+plan\b", re.IGNORECASE),
        (r"/deckhand:plan\b", re.IGNORECASE),
        ("resume-context", re.IGNORECASE),
        (r"\{\{", 0),
        ("_commands", re.IGNORECASE),
        ("Repository block", re.IGNORECASE),
        (r"\bActions\b", 0),
        (r"\bworkflows?\b", re.IGNORECASE),
        ("Fable", re.IGNORECASE),
        ("plan-materialize", re.IGNORECASE),
        ("plan-drift", re.IGNORECASE),
        ("analogy-table", re.IGNORECASE),
        ("issue-create", re.IGNORECASE),
        ("lens-select", re.IGNORECASE),
        ("review-context", re.IGNORECASE),
    )
]

# A step is reached through `next` and nowhere else, so nothing a person reads may name one as a
# command to type; nor may anything name the Approved reply, which the review conversation replaced.
_STEP_COMMAND = re.compile(rf"/deckhand:({'|'.join(STEPS)})\b")
_APPROVED = re.compile(r"\bApproved\b")

_INJECTION = re.compile(r"^!`(.+)`\s*$", re.MULTILINE)
_INVOCATION = re.compile(r'deckhand"?\s+([a-z][a-z0-9-]*)')
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def _split(text: str) -> tuple[dict[str, str], str]:
    """`(frontmatter, body)`; the frontmatter is the `key: value` lines between the `---` fences."""
    assert text.startswith("---\n"), "no frontmatter fence"
    head, _, body = text[4:].partition("\n---\n")
    front = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        assert sep, f"not a key: value line: {line!r}"
        front[key.strip()] = value.strip().strip('"')
    return front, body


@pytest.fixture(params=SKILLS, ids=lambda path: path.name)
def skill(request):
    """One skill directory, with its parsed frontmatter and body."""
    path = request.param
    front, body = _split((path / "SKILL.md").read_text(encoding="utf-8"))
    return path.name, front, body


def test_every_skill_is_named_for_its_directory(skill):
    name, front, _ = skill
    assert front.get("name") == name


def test_every_description_is_one_sentence(skill):
    _, front, _ = skill
    description = front.get("description", "")
    assert description, "no description"
    assert len(_SENTENCE_END.findall(description)) == 1, description


def test_every_skill_may_run_the_launcher(skill):
    _, front, _ = skill
    assert GRANT in front.get("allowed-tools", "")


def test_no_skill_grants_the_bare_launcher(skill):
    """A skill runs deckhand through the plugin root; a bare name on PATH is a different program."""
    _, front, _ = skill
    assert BARE_GRANT not in front.get("allowed-tools", "")


def test_every_skill_injects_its_own_step_once(skill):
    name, _, body = skill
    assert name in INJECTS, f"add {name} to INJECTS"
    injections = _INJECTION.findall(body)
    assert len(injections) == 1, injections
    assert INJECTS[name] in injections[0]


def test_no_skill_names_a_retired_command_or_file(skill):
    _, _, body = skill
    assert [name.pattern for name in RETIRED if name.search(body)] == []


def test_no_skill_names_a_step_command_or_the_approved_reply(skill):
    """A skill is read by a person as well as by Claude, and a step is never a person's to type."""
    name, front, body = skill
    text = "\n".join([*front.values(), body])
    assert not _STEP_COMMAND.search(text), name
    assert not _APPROVED.search(text), name


def _surface() -> set[str]:
    """Every command name the CLI answers to."""
    return set(next(a for a in cli.build_parser()._actions if getattr(a, "choices", None)).choices)


def test_every_command_a_skill_invokes_still_exists(skill):
    _, _, body = skill
    assert sorted(set(_INVOCATION.findall(body)) - _surface()) == []


def test_the_typed_commands_are_the_ones_the_model_never_picks(skill):
    """A command that writes to GitHub is typed; every other skill is the model's to pick."""
    name, front, _ = skill
    if name in TYPED:
        assert front.get("disable-model-invocation") == "true", name
        assert "user-invocable" not in front, name
    else:
        assert "user-invocable" not in front and "disable-model-invocation" not in front, name


def test_no_skill_shows_command_output_to_the_person(skill):
    """What a command printed is Claude's to read; the person hears it in words."""
    _, _, body = skill
    for phrase in ("code block", "verbatim", "Next:"):
        assert phrase not in body, phrase


def test_the_document_skill_reads_right_with_no_arguments(skill):
    """Both of its arguments are optional, so no sentence may render an empty pair of backticks."""
    name, _, body = skill
    if name != "document":
        return
    assert "``" not in body.replace("$mode", "").replace("$topic", "")


def test_every_skill_stays_under_its_word_limit(skill):
    name, _, body = skill
    assert len(body.split()) < WORD_LIMITS.get(name, DEFAULT_WORD_LIMIT), name


def test_the_hidden_skills_are_gone():
    assert sorted(p.name for p in SKILLS) == ["commit", "document", "new", "next", "setup"]


def test_the_next_skill_speaks_and_asks_with_a_recommendation():
    """The conversation guide: one section per step, a recommendation with every question."""
    _, body = _split((ROOT / "skills" / "next" / "SKILL.md").read_text(encoding="utf-8"))
    for heading in (
        "## Speaking",
        "## Review",
        "## Board",
        "## Check and build",
        "## Branch review",
        "## Pull request",
        "## After the merge",
    ):
        assert heading in body, heading
    assert RECOMMEND.search(body)
    assert "verdict" in body
    assert "tuicr -r origin/main..HEAD" in body and "tuicr -r <last reviewed commit>..HEAD" in body
    assert "--stdout" not in body  # the export is pasted either way, and the flag needs a terminal


def test_the_next_skill_asks_for_the_number_it_was_not_given():
    """The argument is optional in the hint, so the skill has to say what to do without one."""
    front, body = _split((ROOT / "skills" / "next" / "SKILL.md").read_text(encoding="utf-8"))
    assert front.get("argument-hint") == "<issue-number>"
    assert "If no number was given, ask which story." in body
    for grant in ("Skill(deckhand:*)", "Skill(superpowers:*)"):
        assert grant in front.get("allowed-tools", ""), grant


def test_the_new_skill_runs_the_brainstorm_aimed_at_a_story():
    _, body = _split((ROOT / "skills" / "new" / "SKILL.md").read_text(encoding="utf-8"))
    assert "superpowers:brainstorming" in body
    assert "superpowers:writing-plans" in body
    assert "Draft" in body


# --- agents ------------------------------------------------------------------


@pytest.fixture(params=AGENTS, ids=lambda path: path.stem)
def agent(request):
    """One agent file, with its parsed frontmatter and body."""
    path = request.param
    front, body = _split(path.read_text(encoding="utf-8"))
    return path.stem, front, body


def test_every_agent_is_named_for_its_file(agent):
    name, front, _ = agent
    assert front.get("name") == name


def test_every_agent_description_is_one_sentence(agent):
    _, front, _ = agent
    description = front.get("description", "")
    assert description, "no description"
    assert len(_SENTENCE_END.findall(description)) == 1, description


def test_every_agent_names_a_model(agent):
    """An agent runs on whatever the session runs on unless it says otherwise, and these say."""
    _, front, _ = agent
    assert front.get("model")


def test_no_agent_names_a_step_command_or_the_approved_reply(agent):
    """An agent's description is a menu entry, so it points at the command a person actually types."""
    name, front, body = agent
    text = "\n".join([*front.values(), body])
    assert not _STEP_COMMAND.search(text), name
    assert not _APPROVED.search(text), name


def test_every_agent_stays_under_the_word_limit(agent):
    _, _, body = agent
    assert len(body.split()) < DEFAULT_WORD_LIMIT


def test_the_author_runs_the_deckhand_its_message_hands_it():
    """No substitution reaches an agent body, so the launcher is a placeholder the message fills."""
    front, body = _split((ROOT / "agents" / "author.md").read_text(encoding="utf-8"))
    assert "new context" in body
    assert "new apply --stub" in body
    assert BARE_GRANT not in front.get("tools", "")
    assert [name for name in _INVOCATION.findall(body) if name in _surface()] == []
