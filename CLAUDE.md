# deckhand

## Tech Stack

- Python package: `deckhand/`, standard library only, behind `bin/deckhand`, a uv script entry
  point that runs `deckhand/cli.py`
- `cli.py` dispatches `<command> context|apply` to the registry, prints a `Refusal` or a Linear
  error on stderr at exit 1, and never prints the text of an unexpected exception; `step.py` the
  registry, `Refusal`, and the shared TOML read; one module per step in `deckhand/steps/`, each
  registered by name, `setup.py` the team, states, automation, labels and repositories
- `ref.py` a story reference such as `ACME-12`; `records.py` the typed records an issue holds;
  `credentials.py` the app and personal Linear credentials, their files private to the owner, and
  the cached app token
- `linear/` every Linear call: `client.py` the GraphQL client and its errors, `oauth.py` the app
  token, `queries.py` every query and the page size, `workspace.py` what setup reads and plans for
  a team, `connect.py` a client for the app or the person
- `store/` the issue store: `__init__.py` the `Store` protocol, its ids Linear UUIDs; `fake.py` the
  in-memory store the tests use; `linear.py` the store backed by Linear
- `ops.py` the idempotent ensure operations over any store, with deterministic ids; `policy.py` a
  repository's `.claude/deckhand.toml`; `config.py` the per-person local config; `github.py` every
  `gh` call
- Skills: `skills/<name>/SKILL.md` with YAML frontmatter; the `next` skill carries the guidance for
  every step; lenses in `skills/next/lenses/`; agents `agents/<name>.md`
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
- Tests: one module per module under test, fakes for Linear and `gh` passed in, `acme/widgets` in
  the fixtures; an apply asserts the exact recorded calls, a refusal asserts no write
- Skills: short bodies, judgment over directive stacks, no capitalized emphasis, one injection line,
  each under its own word limit
- Severity P1, P2, P3; a clean result is `Nothing found.`
- kebab-case file names; no em dashes or en dashes in prose
- Commit messages: subject under 72, body as paragraphs on their own lines, no trailers of any
  kind, ever; `.gitlint` enforces the rest, at commit-msg only, so never commit with `--no-verify`

## Releases

Nothing is committed to `main`. Every change is a branch and a pull request, squash merged, so the
pull request title becomes the commit subject and is a conventional subject under 72 characters. A
change to this repository is not a deckhand story; its design and plan stay in the scratchpad.

The release is the `chore(release): X.Y.Z` pull request that release-please opens and keeps up to
date as work merges. It bumps the version in `pyproject.toml`, `deckhand/__init__.py`,
`.claude-plugin/plugin.json` and `.release-please-manifest.json`, and writes the `CHANGELOG.md`
entry. Merging it creates the tag and the GitHub Release. The number is computed from the
conventional types that landed: `fix` a patch, `feat` a minor, `!` or `BREAKING CHANGE` a major.

`uv.lock` records the project's own version too, and release-please does not bump it, so the release
workflow relocks the open release branch on every push to `main`. CI installs with
`uv sync --frozen`, and a lock behind the version fails `tests/test_cli.py`.

The changelog hides `refactor`, `chore`, `test`, `build`, `style` and `ci`, so a change of one of
those types opens no release on its own and ships with the next one. A refactor of what users
install reaches them when the next fix or feature does.

`tests/test_cli.py` fails when the version strings disagree.

## Common Mistakes

- Do not fetch context by instruction when `deckhand` can inject it
- A status is set only by a step's `apply`, never by prose
- A step is reached through `next` and nowhere else; nothing a person reads names a step command
- Nothing a person writes on GitHub is read; every decision comes from the session
- The Feature field's options, their descriptions and their order are the one thing a person sets
  on GitHub that deckhand reads
- The log is written and never decided from: the column says where a story is, the `deckhand` label
  says whose it is, and a git ref says which commit the review passed. Reading prose to print prose
  stays fine
- The timestamps of log entries may be measured for a forecast, which prints and decides nothing
- Skills call `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand"`; nothing else in a skill runs deckhand
- Do not hardcode organization, project, or repository values in code or tests; only the plugin
  manifests and the install commands carry them
- A blocker is `owner/name#M`; a bare number is this repository
- A parked feature is written in place and becomes the story; only finished work closes an issue
- A feature is an option of the project's Feature field, never an issue: the board holds only
  units of work, and a story of any repository joins one by its Feature value. The option order is
  the pipeline order, and `updateProjectV2Field` keeps an item's value only for an option resent with
  its id
- The build happens in the story's worktree, which `start` makes and `next` names; a branch is
  never checked out by hand, and the clone stays on main
- Several agents may share one checkout's index: commit with an explicit pathspec
  (`git commit -- <files>`), never `git add -A`, `git commit -a`, or a bare `git stash`, and let only
  the orchestrator commit. pre-commit stashes unstaged edits, so run it only when no other agent is
  editing
