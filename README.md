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

`/deckhand:next N` carries the story on from wherever it is, through as many steps as your answers
allow, and stops where a decision is yours: the review's verdict and findings, explained plainly with
what accepting each would change; the story read back and the board question; a check of the plan
against today's code before the build; the branch review in your own terminal after the build, until
a pass is clean; the pull request text; the merge; the items that can only happen after it. Between
stops you see a line when something takes a while. When you come back later, `next N` says where the
story is and picks up there.

You never act on GitHub. You open it to read.

`/deckhand:commit` writes a conventional commit for the staged changes and `/deckhand:document`
records what a branch taught in `docs/claude/`; both are there when you want them on their own.

## The story

A story is one GitHub issue, written by Claude and frozen once the build starts. Its comments are the
log: one entry per decision, so anyone can read the issue top to bottom and see what was planned,
what was decided, and why. Every story is on the board from the moment it is written, and moves from
Draft to Done as the work does. Nothing about a story is written to the repository, and a branch
reaches GitHub only with its pull request, after every line has been through you.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Built by [JSA+Partners](https://jsapartners.co).
