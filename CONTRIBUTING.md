# Contributing

Open an issue before a large change, so the shape is agreed before the work.

## Working on it

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check .
claude plugin validate .
claude --plugin-dir .        # try the skills in a repository; /reload-plugins after edits
```

`CLAUDE.md` describes the layout and the rules the tests enforce. The scripts are standard-library
Python under `deckhand/`; every step has a test module of its own under `tests/`, driven through the
fake `gh` in `tests/fakes/`.

## Pull requests

Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), with no
trailers; the commit hook lints them. A commit body and a pull request body both wrap at 72 columns.
Pull requests are squash merged, and the pull request title and body become the commit on `main`, so
write them as one. Keep a pull request to one change.

## Releases

Every change on `main` that users install is released: the version is bumped in all five places it
is written, `pyproject.toml`, `deckhand/__init__.py`, `.claude-plugin/plugin.json`, and both
`version` fields of `.claude-plugin/marketplace.json`, in one `chore(release): X.Y.Z` commit, tagged
`vX.Y.Z`. Semver follows the conventional type of the change: `fix` is a patch, `feat` a minor, and
a breaking change a major.
