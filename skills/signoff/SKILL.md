---
name: signoff
description: "Walk the person through one story's sign-off, one screen at a time, from what changed to the quiz, and record their decision."
arguments: story team_key
argument-hint: "<story> <team-key>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(deckhand *) Read Grep Glob AskUserQuestion
---

# Sign-off $story

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" signoff context $story --team-key "$team_key"`

Above: the five screens of the sign-off and what is still missing. REF is $story. Talk to the
person plainly, one screen at a time: show it, wait for them, move on. Never mark an option
recommended.

Every decision runs the same way: run
`deckhand decide context KIND REF --team-key "$team_key"`, pass the JSON it prints unchanged to the
question dialog, then run `deckhand decide apply NONCE --team-key "$team_key"` once answered. Save
each answer when it is given, so stopping is safe.

First take what the hand left for the person. In
`deckhand review context REF --stage plan --team-key "$team_key"` and its `--stage branch` twin, an
open finding that is a P1 or changes delivery is decided with
`deckhand decide context finding REF --finding ID --team-key "$team_key"`. A stage stopped at its cap
is decided with `deckhand decide context review-cap REF --stage branch --team-key "$team_key"`, or
`--stage plan`. When the context above shows no change summary, the hand is not done: stop here.

Then show each screen in order with
`deckhand signoff context REF --screen 1 --team-key "$team_key"`, and 2 to 5 the same way: what
changed, evidence, findings, tuicr, quiz.

For the tuicr screen, run `deckhand signoff apply REF review --team-key "$team_key"` first. It builds
a commit of the selected hunks and prints the tuicr command; give it to the person as a plain
message, with each hunk's reason. Once tuicr is open, run the same apply again to seed the reasons as
comments. A comment they leave is work for the hand.

For the quiz screen, ask each open item with
`deckhand decide context quiz REF --item 1 --team-key "$team_key"`, then 2 and on. A wrong answer
prints the code that answers it: show that code, then ask the item again until it is right.

Last, run `deckhand signoff context REF --team-key "$team_key"` again. With nothing missing, decide
`deckhand decide context sign-off REF --team-key "$team_key"`. On "Sign off", tell the person the
hand merges it next, pinned to this head. On "Not yet", say in a sentence what the hand should
change; any new commit needs review and sign-off again.
