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
trailers; the commit hook lints them. A commit body and a pull request body are paragraphs on their
own lines, never wrapped.
Pull requests are squash merged, and the pull request title and body become the commit on `main`,
so write them as one: the title is a conventional subject under 72 characters, because the version
and the release notes are built from it. Keep a pull request to one change.

## Releases

Releases are prepared by [release-please](https://github.com/googleapis/release-please). It reads
the conventional commits on `main` and keeps one `chore(release): X.Y.Z` pull request up to date,
carrying the version bump and the `CHANGELOG.md` entry. Merging that pull request creates the tag
and the GitHub Release. Nothing is tagged by hand.
