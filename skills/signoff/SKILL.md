---
name: signoff
description: "Walk the person through one story's sign-off, one screen at a time, from what changed to tuicr, and record their decision."
arguments: story
argument-hint: "<story>"
effort: high
allowed-tools: Bash(deckhand *) Read Grep Glob AskUserQuestion
---

# Sign-off $story

!`deckhand signoff context $story`

Above: the five screens of the sign-off and what is still missing. REF is $story. Talk to the
person plainly. Never mark an option recommended.

Every decision runs the same way: run `deckhand decide context KIND REF`, pass the JSON it
prints unchanged to the question dialog, then run `deckhand decide apply NONCE` once answered.

First take what the hand left for the person. Decide each finding the `missing:`
lines name, and each open P1 in `deckhand review context REF --stage plan` and
its `--stage branch` twin, with `deckhand decide context finding REF --finding ID`.
For a finding the hand left open, Decline keeps the hand's call and Accept sends
it back, which makes the sign-off "Not yet". A stage stopped at its cap
is decided with `deckhand decide context review-cap REF --stage branch`, or
`--stage plan`. When the context above shows no change summary, the hand is not done: stop here.

Then show screens 1 to 4 in one reply, each from `deckhand signoff context REF --screen N`:
what changed, evidence, findings, what to check. Tuicr stays a separate step, waiting for the
person.

For the tuicr screen, run `deckhand signoff apply REF review`. When it selects no hunk, go to the
sign-off question. Otherwise show the person its lines, then decide `deckhand decide context tuicr
REF`, even when the person says it is done; unless Done is recorded, stop. On Done, read
`deckhand signoff context REF --screen 5`. Ask about each comment whether it is work for the hand;
if one is, say so before the sign-off question.

Last, decide `deckhand decide context sign-off REF`. While anything is missing it offers
"Not yet" and "Back"; "Back" records nothing. Run it early for hand work. The answer to this question is final: never ask
again. On "Sign off", tell the person the hand merges it next, pinned to this head. On "Not yet",
say in a sentence what the hand should change; any new commit needs review and sign-off again.
