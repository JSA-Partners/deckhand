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
    "amend": "amend context",
    "commit": "commit context",
    "document": "document context",
    "finish": "finish context",
    "new": "new context",
    "ready": "ready context",
    "review": "review context",
    "setup": "setup context",
    "start": "start apply",
}

# The skills a person invokes and nothing else does. The other three are called by a skill:
# start and finish run commit and document, and both of those and amend answer a step's own prose.
USER_DRIVEN = {"setup", "new", "review", "ready", "start", "finish"}

GRANT = 'Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *)'
BARE_GRANT = "Bash(deckhand *)"
WORD_LIMIT = 200

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
        ("resume", re.IGNORECASE),
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


def _surface() -> set[str]:
    """Every command name the CLI answers to."""
    return set(next(a for a in cli.build_parser()._actions if getattr(a, "choices", None)).choices)


def test_every_command_a_skill_invokes_still_exists(skill):
    _, _, body = skill
    assert sorted(set(_INVOCATION.findall(body)) - _surface()) == []


def test_only_the_user_driven_skills_are_hidden_from_automatic_invocation(skill):
    """A step a person starts is never started for them; the ones a step calls stay invocable."""
    name, front, _ = skill
    hidden = front.get("disable-model-invocation") == "true"
    assert hidden == (name in USER_DRIVEN), name


def test_every_skill_stays_under_the_word_limit(skill):
    _, _, body = skill
    assert len(body.split()) < WORD_LIMIT


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


def test_every_agent_stays_under_the_word_limit(agent):
    _, _, body = agent
    assert len(body.split()) < WORD_LIMIT


def test_the_author_runs_the_deckhand_its_message_hands_it():
    """No substitution reaches an agent body, so the launcher is a placeholder the message fills."""
    front, body = _split((ROOT / "agents" / "author.md").read_text(encoding="utf-8"))
    assert "new context" in body
    assert "new apply --stub" in body
    assert BARE_GRANT not in front.get("tools", "")
    assert [name for name in _INVOCATION.findall(body) if name in _surface()] == []
