# deckhand

## Tech Stack

- Python package: `deckhand/`, standard library only, behind `bin/deckhand`, a uv script entry
  point that runs `deckhand/cli.py`
- `cli.py` dispatches `<command> context|apply` to the registry, prints a `Refusal`, a Linear error
  or a git error on stderr at exit 1, and never prints the text of an unexpected exception;
  `step.py` the registry, `Refusal`, and the shared TOML read
- `steps/` one module per command, each registered by name: setup, the feature and its planning
  (`feature`, `plan`, `stories`, `sitting`, `decide`, `hook`), and the build (`build`, `implplan`,
  `review` and `feature-review`, `checks`, `quiz`, `signoff`, `merge` and `after`), a hand's
  planning work (`spike`, `draft`), its questions (`ask`) and its messages (`mail`), and the
  person's fleet (`hand` lists the fleet and asks the engine for a spike, `captain` prints the
  brief, the top item and whether the engine runs from the snapshot alone, the status line segment
  and the board, `engine` says whether the engine runs and how to start it, and runs it with its
  screen, `watch` rings a session's doorbell and beats the captain's watcher); `commit` and
  `document` only report
- The core modules beside them hold the rules the steps share and never print: features and their
  phases, decisions and their pending answers, versions of held records, tiers, implementation plans
  and their checks, review rounds and their stops, lenses, checks, the change summary, tuicr, the
  quiz and the sign-off; `queue.py` the person's ranked items, `fleet.py` the Claude Code sessions
  and their slots, `startable.py` which stories a hand may start and when one is due, `claims.py`
  who owns a story, `asks.py` a hand's questions and wakes, `wakes.py` the text of the messages a
  hand is sent, `gaps.py` permission gaps, `spikes.py` spike questions and answers, `order.py` the
  build order, `answers.py` the dialog answers the hook stores, `inputs.py` the files a hand may
  hand in; `stage.py` where each story is in the build and its review round, `reading.py` the full
  read the engine makes, `status.py` the snapshot kept in deckhand's local SQLite file,
  `standing.py` how the snapshot and the fleet sort into needs you, working, stopped, queued, team
  and done today, `brief.py` the brief and the status line segment, `board.py` the board's sections
  and rows; `watch.py` when a session's watcher rings and when the captain's has gone silent
- The engine: `reconcile.py` turns the fleet, the local rows and the last Linear read into actions
  and writes nothing, `acts.py` carries one action out and builds every hand's launch, `worker.py`
  runs them in order on one thread, `poller.py` reads the fleet and Linear on their own threads,
  `linker.py` the Linear read the engine takes from that thread, `lines.py` the failures the
  engine shows, and `engine.py` is the loop, its lock, its heartbeat and the snapshot it writes
- `monitors/monitors.json` registers `watch.sh`, the watcher every session runs; in a session that
  is neither captain nor hand it parks rather than exits, since an exit costs a model turn
- `frame.py` lays the board out to a width and `tui.py` draws it full screen, row by row in plain
  escape codes; `statusline.py` installs and removes the captain's segment in the person's status
  line, with a backup, and writes the `deckhand` launcher in deckhand's config directory; `hosts.py`
  what each status line tool needs, and the launcher scripts
- `linear/` every Linear call: the GraphQL client and its errors, the meter that reads its budget
  headers, the app token, every query, what setup reads and plans for a team, and a client for the
  app or the person; `budget.py` the Linear budget each role's meter keeps in the local state
- `store/` the issue store: the `Store` protocol with Linear UUIDs for ids, the in-memory fake the
  tests use, and the store backed by Linear; `local.py` the records in deckhand's local SQLite
  file, `state.py` its message, hand and engine rows; `ops.py` the idempotent ensure operations over
  any store, with deterministic ids
- `git.py` every git call and `gh.py` every `gh` call, each behind an injected runner; `policy.py`
  a repository's `.claude/deckhand.toml`; `config.py` and `credentials.py` what stays on one
  person's machine; `launch.py` the sandboxed settings a hand or the captain runs under
- Skills: `skills/<name>/SKILL.md` with YAML frontmatter: a hand's (story, spike, draft) and
  the person's (setup, captain, feature, signoff, commit, document); review lenses in `skills/story/lenses/`, a project's own in its
  `.claude/deckhand/lenses/`; agents `agents/<name>.md`
- `README.md` the process; `skills/document/reference.md` the shape of `docs/claude/` files

## Purpose

- The glue between superpowers, impeccable and Linear, where the story process lives
- Never reimplements a superpowers or impeccable skill; never encodes language or framework facts

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
- Agents hand deckhand JSON in the shape its context prints; markdown is for people
- Every question deckhand builds fits the dialog: two to four options in a fixed order, a short
  header, none marked recommended
- Python: `from __future__ import annotations`, type hints on public functions, no module over 400 lines
- Tests: one module per module under test, fakes for Linear, git and `gh` passed in, `acme/widgets`
  in the fixtures; an apply asserts the exact recorded calls, a refusal asserts no write
- Skills: short bodies, judgment over directive stacks, no capitalized emphasis, one injection line,
  each under its own word limit, every named command written so it parses;
  `tests/test_skills.py` enforces the shape
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
- A status is never written by deckhand; Linear moves a story from the pull request: draft to In
  Progress, ready to In Review, merged to Done
- Every decision comes from a recorded dialog answer in the person's session, never from anything
  else; a hand never asks, it records a question with `ask` and stops until its message arrives
- A hand is a background session per story, draft or spike, under settings that hold no
  credential; every write it causes goes through `deckhand`, and a raw write fails
- Sessions never message each other: a row in deckhand's local SQLite file is the message, and a
  session's watcher, a plugin monitor, is only the doorbell
- Only the engine starts, resumes or stops a hand; a waiting hand frees its slot, a planning hand
  holds one
- Only the engine writes the snapshot, for the captain, the status line and the board, and nothing
  decides from it
- A hand does the after-merge chores it can; only the rest reach the person's queue
- `.claude/` is Claude Code's: a hand may edit `.claude/deckhand.toml` and
  `.claude/deckhand/lenses/`, and any other change becomes a queue item through `ask apply --edit`
- `checks apply` runs a hand's tests and check commands with deckhand's access, a gap the README
  names
- Records live in deckhand's local SQLite file; Linear holds only what a teammate reads, and documents
  hold only text a person reads
- Never parse prose to decide; read typed records, and print prose only for people
- Every skill calls the bare `deckhand`, its injection line included, which the plugin puts on the
  path, so each call matches the sandbox's exempt `deckhand *` pattern; a chained, piped or
  prefixed call runs sandboxed and fails, and hooks keep the full plugin path
- Never print a credential or the text of an unexpected exception; a `Refusal` rule carries no secret
- Do not hardcode organization, team, or repository values in code or tests; only the plugin
  manifests and the install commands carry them
- The build happens in the story's worktree; a branch is never checked out by hand, and the clone
  stays on main
- Review rounds are computed from records: never rerun on an unchanged target, scope a revision to
  its diff, stop when the last round holds no accepted P1, and leave the cap to the person only
  while a P1 is open
- Findings are ordered by confidence and never deleted; a doubtful one is folded, and only the
  person declines one
- Sign-off, the change summary, the quiz and the checks are pinned to one head; merging is pinned to
  the signed head, never auto-merge, and a moved head or a failed check voids the sign-off
- Several agents may share one checkout's index: commit with an explicit pathspec
  (`git commit -- <files>`), never `git add -A`, `git commit -a`, or a bare `git stash`, and let only
  the orchestrator commit. pre-commit stashes unstaged edits, so run it only when no other agent is
  editing
