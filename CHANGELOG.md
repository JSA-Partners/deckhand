# Changelog

## [4.0.0](https://github.com/JSA-Partners/deckhand/compare/v3.12.1...v4.0.0) (2026-09-28)


### ⚠ BREAKING CHANGES

* `captain apply --repair` no longer writes `Status` or adds the `deckhand` label. It boards a story the process owns that is off the board, and refuses when there is none. `DECKHAND_SETTLE` is gone, along with the wait it tuned.
* the split file is JSON and named `.json`. A split in progress has to be rewritten: `{"kind": "split", "requirements": "...", "stories": [{"key": "...", "title": "...", "sentence": "...", "repo": "owner/name", "after": ["<another key>"]}]}`. `repo` and `after` stay optional.
* the findings, verdicts and decisions files are JSON and named `.json`. A review in progress has to be rewritten. Each file opens with its own kind:
* a story that is not on the board is asked for a review rather than boarded. No column means nothing is known about it, whatever its comments say. Run `captain apply --repair` to add a story the board is missing. The clean pass is recorded with `reviewed apply <n> "<one line>"` in place of a `Reviewed:` log entry, and the gate reads a ref this clone holds, so a clone that never ran the review refuses until it does.
* the `Status` option `Pending Review` is now `In Review`. Rename it in the project settings rather than deleting and recreating it, because a rename keeps the option id and every item stays in its column. Until it is renamed, `setup` and `captain context` name the mismatch and any step that writes the column refuses with the list of options the project offers.

### Features

* a split is written as JSON, and after names keys ([#15](https://github.com/JSA-Partners/deckhand/issues/15)) ([7e065e9](https://github.com/JSA-Partners/deckhand/commit/7e065e9dd466df6db333f730f53fff3f81bcc6a1))
* add the columns and the label the story state will live in ([#5](https://github.com/JSA-Partners/deckhand/issues/5)) ([6229221](https://github.com/JSA-Partners/deckhand/commit/6229221dd19a9f6b6a678868b9d66fcf9e9a1888))
* mark the stories the process wrote to ([#9](https://github.com/JSA-Partners/deckhand/issues/9)) ([7b4cb9d](https://github.com/JSA-Partners/deckhand/commit/7b4cb9d76f99b36c526b616fd74174572e97ebd0))
* one read per fact, for the pull request and for git ([#13](https://github.com/JSA-Partners/deckhand/issues/13)) ([01850b8](https://github.com/JSA-Partners/deckhand/commit/01850b87e47e4bc8726d69d7bee10674b5109174))
* rename Pending Review to In Review, with one name per column ([#11](https://github.com/JSA-Partners/deckhand/issues/11)) ([97ed970](https://github.com/JSA-Partners/deckhand/commit/97ed970a7112678de93a45d09b726193a9a86b0e))
* take a story's state from its column, not its log ([#12](https://github.com/JSA-Partners/deckhand/issues/12)) ([8de45ea](https://github.com/JSA-Partners/deckhand/commit/8de45ead1f9146114b15fe164c4fa1a784a8f8bd))
* the column decides, the label owns the story, a draft is a draft ([#16](https://github.com/JSA-Partners/deckhand/issues/16)) ([5cbe470](https://github.com/JSA-Partners/deckhand/commit/5cbe4704e25c37045765c4179ff26238c5afd028))
* the review round is JSON, written and read ([#14](https://github.com/JSA-Partners/deckhand/issues/14)) ([86000ac](https://github.com/JSA-Partners/deckhand/commit/86000ac0a952362f2c5ad640f0e7006f3e2080ed))
* write the column at every step, including past the merge ([#8](https://github.com/JSA-Partners/deckhand/issues/8)) ([ba29247](https://github.com/JSA-Partners/deckhand/commit/ba29247708eddcb4b4309f777f67bb1c8d18947c))


### Bug Fixes

* keep a repair to the stories the process owns ([#10](https://github.com/JSA-Partners/deckhand/issues/10)) ([b607d00](https://github.com/JSA-Partners/deckhand/commit/b607d005fd6d6db1256204f6c8a28ae42b68611c))
* record the clean pass on the story's own branch ([#18](https://github.com/JSA-Partners/deckhand/issues/18)) ([d192889](https://github.com/JSA-Partners/deckhand/commit/d192889bcfa6e60014966e2df2b2d703042ce262))
* treat no labels as none and an unreported workflow as unknown ([#7](https://github.com/JSA-Partners/deckhand/issues/7)) ([fcabacc](https://github.com/JSA-Partners/deckhand/commit/fcabacc19b99008435a348f5751c6ff7e3a58647))


### Documentation

* the contract describes the code this release ships ([#17](https://github.com/JSA-Partners/deckhand/issues/17)) ([84840f1](https://github.com/JSA-Partners/deckhand/commit/84840f10ced1b840bf2d3c4738962a2d1c2e82d5))

## [3.12.1](https://github.com/JSA-Partners/deckhand/compare/v3.12.0...v3.12.1) (2026-09-26)


### Bug Fixes

* name what a refusal expects and where a long context goes ([#2](https://github.com/JSA-Partners/deckhand/issues/2)) ([97bcff5](https://github.com/JSA-Partners/deckhand/commit/97bcff548e33f7f7f62d124f3f76075bcf49e397))
