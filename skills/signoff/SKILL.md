---
name: signoff
description: "Walk the person through one story's sign-off, one screen at a time, from what changed to the quiz, and record their decision."
arguments: story
argument-hint: "<story>"
effort: high
allowed-tools: Bash(deckhand *) Read Grep Glob AskUserQuestion
---

# Sign-off $story

!`deckhand signoff context $story`

Above: the five screens of the sign-off and what is still missing. REF is $story. Talk to the
person plainly, one screen at a time: show it, wait for them, move on. Never mark an option
recommended.

Every decision runs the same way: run
`deckhand decide context KIND REF`, pass the JSON it prints unchanged to the
question dialog, then run `deckhand decide apply NONCE` once answered. Save
each answer when it is given, so stopping is safe.

First take what the hand left for the person. Decide each finding the `missing:`
lines name, and each open P1 in `deckhand review context REF --stage plan` and
its `--stage branch` twin, with `deckhand decide context finding REF --finding ID`.
For a finding the hand left open, Decline keeps the hand's call and Accept sends
it back, which makes the sign-off "Not yet". A stage stopped at its cap
is decided with `deckhand decide context review-cap REF --stage branch`, or
`--stage plan`. When the context above shows no change summary, the hand is not done: stop here.

Then show each screen in order with
`deckhand signoff context REF --screen 1`, and 2 to 5 the same way: what
changed, evidence, findings, tuicr, quiz.

For the tuicr screen, run `deckhand signoff apply REF review --watch` in the
background. Its first lines are the tuicr command and each file's reason: give them to the person
as a plain message. It seeds the reasons once tuicr opens and ends when tuicr closes, printing the
person's own comments. Ask about each whether it is work for the hand, which makes the sign-off
"Not yet"; with none, go on to the quiz. On a timeout line, run it again.

For the quiz screen, ask each open item with
`deckhand decide context quiz REF --item 1`, then 2 and on. A wrong answer
prints the code that answers it: show that code, then ask the item again until it is right.

Last, run `deckhand signoff context REF` again. With nothing missing, decide
`deckhand decide context sign-off REF`. On "Sign off", tell the person the
hand merges it next, pinned to this head. On "Not yet", say in a sentence what the hand should
change; any new commit needs review and sign-off again.
