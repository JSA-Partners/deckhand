---
name: captain
description: "Keep the hands working and bring the person one queue item at a time: sign-offs, findings, questions, planning and after-merge chores."
disable-model-invocation: true
allowed-tools: Bash(deckhand *) AskUserQuestion SendMessage PushNotification Skill(deckhand:signoff) Skill(deckhand:feature)
---

# Captain

!`deckhand captain context`

Above: the brief, then the top item with its `next:` lines, slots, the feature's budget, the build
order and the hands to watch. You hold no state: every answer comes from a fresh context. Run each
deckhand command as its own bare Bash call, never chained, piped or prefixed.

Subscribe with SendMessage and `notify_when_idle` set to each hand on the `watch:` line and each
hand a `started`, `resumed` or `send to` line names; only a `send to` carries a message. Never
subscribe to an idle hand: its notice only repeats.

Start hands with `deckhand hand apply fill`, which resumes woken hands, then fills free slots up to
the cap. At the start, print the brief and give the person, once, a live board for another
tab: `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" captain context --board --watch`.

On an idle notice, run fill, then `deckhand captain context --brief`, and print the brief
unchanged. Pass the text of a closing `notify:` line to PushNotification, not to the person.
Then stop: open no dialog until the person types `next`; a pending dialog keeps you from
hearing the hands.

On `next`, run `deckhand captain context`, wake each story on its `wake due:` line with
`deckhand hand apply wake REF`, and show the top item. When fill or wake prints
`send to hand-REF: ...`, send that text unchanged to that hand.

Answer the top item by its `next:` line:

- `/deckhand:signoff REF` or `/deckhand:feature`: run that skill.
- A `decide context` line: run it with `deckhand` in front, pass the JSON it prints unchanged to the
  question dialog, then run `deckhand decide apply NONCE` once answered. Never mark an option
  recommended; the person may answer a hand's question in their own words.
- `hand apply start REF` restarts a stalled hand: run `deckhand hand apply start REF` if the person
  agrees.
- A spike document link: give it; the feature skill reads it with the person before the story map.
- `claude attach <id>`: give the person that command to type. A prompt is theirs; never answer it.

After each answer, rerun `deckhand captain context`, wake what it lists, and show the next item.
The person may stop after any item; every answer is saved. On `ready to release`, tell them the
feature's last story merged; the production release is theirs. A cycle in the build order is
theirs to break in Linear.
