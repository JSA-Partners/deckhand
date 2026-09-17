# deckhand

A story process for [Claude Code](https://code.claude.com/): from feature request to merged pull
request, kept in GitHub issues.

deckhand runs on [superpowers](https://github.com/obra/superpowers), which does the brainstorming,
planning, and implementation. deckhand adds the process around them: one issue per story, a review
you decide in conversation, a board that follows the work, and a pull request that opens only from
a commit you have read.

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

Run `/deckhand:setup` once in each repository you want it in. It does what the GitHub API allows,
checks the rest, and tells you what is left and where to click. Run it again when done.

## Use

You type two things.

`/deckhand:new "<the idea>"` is a conversation: a question or two for a fix, the whole brainstorm for
a feature, a proposed split when the idea is really several stories. It ends with the story on the
board as Draft and its review under way.

`/deckhand:next N` carries the story on from wherever it is and stops where a decision is yours: the
review's findings, the estimate, the plan checked against today's code, your own pass over the
branch, the pull request, the merge. Run it again later and it says where the story is and picks up
there.

You never act on GitHub. You open it to read.

Every story builds in its own git worktree, so several sessions work from one clone, and the
worktree goes when the story merges. Work found mid-build can be parked in any repository of the
project, carrying what the session learned, and can hold this story until it lands.

`/deckhand:captain` watches the whole project rather than one story: every story and its column,
every session working on one, the order to build the backlog in, and anything the board holds that no
step could have written. It reads, and the only things it writes are that order and a status the
board got wrong.

`/deckhand:commit` writes a conventional commit for the staged changes and `/deckhand:document`
records what a branch taught in `docs/claude/`. Both are there when you want them on their own.

## The story

A story is one GitHub issue, written by Claude and frozen once the build starts. Its comments are the
log: one entry per decision, so the issue reads top to bottom as what was planned, what was decided,
and why. It is on the board from the moment it is written and moves from Draft to Done as the work
does. Nothing about a story is written to the repository, and a branch reaches GitHub only with its
pull request, from a commit you have read.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Built by [JSA+Partners](https://jsapartners.co).
