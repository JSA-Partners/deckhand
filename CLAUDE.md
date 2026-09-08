# deckhand

## Tech Stack

- Python package: `deckhand/`, standard library only, behind the entry point `bin/deckhand`
- One module per step, `deckhand/<step>.py`, the story steps with `context` and `apply`, commit and
  document with `context` alone; there is no `plan` step, `new` writes a plan and `amend` rewrites
  one; `step.py` the registration, `Refusal`, `issue_number`, and the shared helpers; `issue.py`
  every issue read and write; `git.py` every git call; `drift.py` the plan references `start` checks
- Skills: `skills/<name>/SKILL.md` with YAML frontmatter; lenses in `skills/review/lenses/`; agents
  `agents/<name>.md`
- `README.md` the process; `skills/document/reference.md` the shape of `docs/claude/` files

## Purpose

- The glue between superpowers and a story process kept in GitHub issues
- Never reimplements a superpowers skill; never encodes language or framework facts

## Development Workflow

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check .
claude plugin validate .
claude --plugin-dir .        # then /reload-plugins after edits
```

## Code Style

- If a step can be computed, it is a `deckhand` command; the model does only what remains
- `context` prints what the model needs and never fails; `apply` validates everything, then writes,
  and a `Refusal` names the failed rule on stderr at exit 1 before any write
- Python: `from __future__ import annotations`, type hints on public functions, no module over 400 lines
- Tests: one module per module under test, the fake `gh` through the `fake_gh` fixture,
  `acme/widgets` in the fixtures; an apply asserts the exact recorded calls, a refusal asserts no
  write
- Skills: short bodies, judgment over directive stacks, no capitalized emphasis, one injection line,
  under 200 words; `tests/test_skills.py` enforces the shape
- Severity P1, P2, P3; a clean result is `Nothing found.`
- kebab-case file names; no em dashes or en dashes in prose
- Commit messages: no trailers of any kind, ever; `.gitlint` enforces the rest, at commit-msg only, so
  never commit with `--no-verify`

## Common Mistakes

- Do not fetch context by instruction when `deckhand` can inject it
- A status is set only by a step's `apply`, never by prose
- Skills call `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand"`; nothing else in a skill runs deckhand
- Bump the version in `pyproject.toml` and both manifests together; nothing checks that they agree
- Do not hardcode organization, project, or repository values in code or tests; only the plugin
  manifests and the install commands carry them
- Several agents may share one checkout's index: commit with an explicit pathspec
  (`git commit -- <files>`), never `git add -A`, `git commit -a`, or a bare `git stash`, and let only
  the orchestrator commit. pre-commit stashes unstaged edits, so run it only when no other agent is
  editing
