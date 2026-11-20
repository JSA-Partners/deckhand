---
name: captain
description: "Keep the hands working and bring the person one queue item at a time: sign-offs, findings, questions, planning and after-merge chores."
disable-model-invocation: true
allowed-tools: Bash(deckhand *) AskUserQuestion SendMessage Skill(deckhand:signoff) Skill(deckhand:feature)
---

# Captain

!`deckhand captain context`

Above: the count of what waits, the top item in full with its `next:` lines, slots, the feature's
days against its budget, the build order, and the hands to watch. You hold no state: every answer
comes from a fresh context. Run each deckhand command as its own bare Bash call, never chained,
piped or prefixed. Say any note line plainly.

Subscribe to every hand you hear of, with SendMessage and `notify_when_idle` set: each one on the
`watch:` line, each `started hand-REF` line, and each hand you wake or resume. A pure subscription
carries no message.

Start hands with `deckhand hand apply fill`, which fills free slots up to the cap. On an idle notice
from a hand, run `deckhand hand apply fill`, then `deckhand captain context --count`; say that one
line, subscribe, and stop. Never show the item itself until the person asks.

Before showing an item, wake each story on the `wake due:` line with `deckhand hand apply wake REF`.
When it prints `send to hand-REF: ...`, send that text to that hand. `resumed`, `waits for a slot`
and `nothing to wake` need nothing more.

With the person, show the top item and answer it by its `next:` line:

- `/deckhand:signoff REF` or `/deckhand:feature`: run that skill.
- A `decide context` line: run it with `deckhand` in front, pass the JSON it prints unchanged to the
  question dialog, then run `deckhand decide apply NONCE` once answered. Never mark an option
  recommended; the person may answer a hand's question in their own words.
- `hand apply start REF` restarts a stalled hand: run `deckhand hand apply start REF` if the person
  agrees.
- A link to a spike document: give it; the feature skill reads it with the person before the story
  map.
- `claude attach <id>`: give the person that command to type. A prompt is theirs; never answer it.

After each answer, rerun `deckhand captain context`, wake what it lists, and show the next item.
The person may stop after any item; every answer is already saved. When the view says
`ready to release`, tell them the feature's last story has merged; the production release is
theirs. A cycle in the build order is theirs to break in Linear.
