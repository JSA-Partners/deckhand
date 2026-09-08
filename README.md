# deckhand

A story process for [Claude Code](https://code.claude.com/): from feature request to merged pull
request, kept in GitHub issues.

deckhand runs on [superpowers](https://github.com/obra/superpowers), which does the brainstorming,
planning, and implementation. deckhand adds the steps around them: one issue per story, one review
with a human approval, a board that follows the work, and a pull request that opens only when every
gate passes.

## Requires

- Claude Code with superpowers installed
- `gh`, authenticated, and Python 3.12 or newer
- A GitHub Project, or setup creates one
- [tuicr](https://github.com/agavra/tuicr), for the branch review before the pull request

## Install

```bash
claude plugin marketplace add obra/superpowers
claude plugin install superpowers@superpowers-marketplace
claude plugin marketplace add JSA-Partners/deckhand
claude plugin install deckhand@jsapartners
```

## Setup

Run `/deckhand:setup` once in each repository you want it in. The setup is guided: it does what the
GitHub API allows and prints a short checklist for the rest. It only adds what is missing, so run it
again after upgrading.

## Use

1. `/deckhand:new` turns a request into a story with its plan, or into several stories when the
   request is a feature.
2. `/deckhand:review N` reviews the story and posts its findings on the issue. Tick the ones you
   accept and reply `Approved`.
3. `/deckhand:ready N` puts the story on the board with its kind, points, and blockers.
4. `/deckhand:start N` branches and implements the plan, one commit per task.
5. `/deckhand:finish N` has you review the branch, then opens the pull request once every gate
   passes.
6. Merge. GitHub closes the issue and sets Done.

Three more commands run when a step needs them: `/deckhand:amend N` changes a story after a review or
a discovery, `/deckhand:commit` writes a conventional commit for the staged changes, and
`/deckhand:document` records what a branch taught in `docs/claude/`. The board's Status follows the
steps; nothing sets it by hand.

## The story

A story is one GitHub issue with five sections: Story, Scope (In and Out), Acceptance Criteria, Plan,
Notes. The issue is the only record of a story; nothing about one is written to the repository.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Built by [JSA+Partners](https://jsapartners.co).
