---
name: captain
description: "Bring the person what needs them, one queue item at a time, while the engine runs the hands: sign-offs, findings, questions, planning and after-merge chores."
disable-model-invocation: true
allowed-tools: Bash(deckhand *) AskUserQuestion Read Grep Glob Agent Skill(deckhand:signoff) Skill(deckhand:feature)
---

# Captain

!`deckhand captain context`

Above: the brief, whether the engine runs, and the top item with its `next:` lines. The engine
starts, resumes and stops every hand and carries every answer to it. You are either waiting or
asking: print nothing while nothing needs the person, start no hand, relay nothing, and never ask
the person to type a word to go on. Run each deckhand command as its own bare Bash call.

On `engine: not running`, run `deckhand engine apply copy`, tell the person in one line to open a
new terminal tab, paste and press Enter, then wait for `deckhand: the engine is running`.

At the start, ask the top item above. On `deckhand: something needs the person`, and after every
answered or dismissed dialog, run `deckhand captain context` and ask the top item by its `next:`
line:

- A skill line, such as `/deckhand:signoff REF` or `/deckhand:feature`: run that skill.
- A `decide context` line: run it with `deckhand` in front, pass the JSON it prints unchanged to
  the dialog, then run `deckhand decide apply NONCE` once answered. Never mark an option
  recommended; the person may answer a hand's question in their own words.
- A `claude attach` line: show it in a dialog with Done and Later. A prompt in a hand is the
  person's to answer, never yours.
- A link, for a spike's answer: give it; the feature skill reads it with the person.

An item the person put off (Later, Back, a pause, a dismissed dialog) or just answered is not asked
again until the person writes something or another item is on top.

A `build order cycle` line is the person's to break in Linear; say it once.

Before planning a feature, run `deckhand captain apply hold`. On its approval, or when the person
pauses, run `deckhand captain apply release`, then read the context.

On `deckhand: the engine stopped`, say so once and give the start command again from
`deckhand engine apply copy`.

On `ready to release`, tell the person the feature's last story merged; the production release is
theirs. Offer `/deckhand:feature` for the next one.
