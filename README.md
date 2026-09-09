# deckhand

A story process for [Claude Code](https://code.claude.com/): from feature request to merged pull
request, kept in GitHub issues.

deckhand runs on [superpowers](https://github.com/obra/superpowers), which does the brainstorming,
planning, and implementation. deckhand adds the steps around them: one issue per story, one review
whose findings you tick, a board that follows the work, and a pull request that opens only when
every gate passes.

## Requires

- Claude Code with superpowers installed
- `gh`, authenticated, and Python 3.12 or newer
- A GitHub Project, or setup creates one
- [tuicr](https://github.com/agavra/tuicr), for the branch review before the pull request

## Install

```bash
claude plugin marketplace add obra/superpowers
claude plugin install superpowers@superpowers-dev
claude plugin marketplace add JSA-Partners/deckhand
claude plugin install deckhand@jsapartners
```

## Setup

Run `/deckhand:setup` once in each repository you want it in. It does what the GitHub API allows and
prints a short checklist for the rest. Run it again after upgrading.

## Use

You type two things.

`/deckhand:new "<the request>"` turns a request into a story with its plan, or into several stories
when the request is a feature; a number writes a stub into its story. It ends with the issue link
and `Next: /deckhand:next N`, one line per story.

`/deckhand:next N` reads the issue and runs the one step that story is due: review it, amend it from
your ticks and replies, board it, branch and build it, or open its pull request. It ends with the
link to look at and a `Next:` line: another `/deckhand:next N` when it is your turn again,
`Next: merge it.` when the pull request is open, or `Next: nothing.` when the story is done. Run it
again whenever you have done your part.

The stops are yours, and they are the only ones:

- the review, on the issue: tick the findings you accept, reply to the rest;
- the board question, which sets kind, points, and blockers;
- the choice, keep building or review and finish, when the branch already has commits;
- the branch review, the first act of finish, one pass per round of comments;
- the pull request text, approved before it opens;
- the merge, on GitHub, which closes the issue; the board's own workflow sets Done.

Between the stops it is an ordinary session: while it builds, talk to Claude, ask why, change course.

`/deckhand:commit` writes a conventional commit for the staged changes and `/deckhand:document`
records what a branch taught in `docs/claude/`; both are there when you want them on their own. The
board's Status follows the steps; nothing sets it by hand.

## The story

A story is one GitHub issue: Story, Scope, Acceptance Criteria, Plan, Notes. The plan is folded on
the issue and frozen once the work starts; what changes after that is a comment or a new issue. The
issue's comments are its log, and nothing about a story is written to the repository.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Built by [JSA+Partners](https://jsapartners.co).
