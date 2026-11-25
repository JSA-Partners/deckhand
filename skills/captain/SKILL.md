---
name: captain
description: "Bring the person what needs them, one queue item at a time, while the engine runs the hands: sign-offs, findings, questions, planning and after-merge chores."
disable-model-invocation: true
allowed-tools: Bash(deckhand *) AskUserQuestion Read Grep Glob Agent Skill(deckhand:signoff) Skill(deckhand:feature)
---

# Captain

!`deckhand captain context`

Above: the brief, whether the engine runs, and the top item with its `next:` lines. You are either
waiting or asking: start no hand, relay nothing, and never ask the person to type a word to go on.
End every turn with one short line the person sees, such as `Nothing waits.` when nothing does, or,
when a Linear error cut it short, that you will ask once Linear is back. Run each deckhand command
as its own bare Bash call. Make input files with Write, never cp; read with Read or Grep, never git.

On `engine: not running`, run `deckhand engine apply copy`, tell the person in one line to open a
new terminal tab, paste and press Enter, then wait for `deckhand: the engine is running`.

At the start, ask the top item above. On `deckhand: something needs the person`, and after every
answered or dismissed dialog, run `deckhand captain context` and ask the top item by its `next:`
line:

- A skill line, such as `/deckhand:signoff REF` or `/deckhand:feature`: run that skill.
- A `decide context` line: run it with `deckhand` in front, pass the JSON it prints unchanged to
  the dialog, then run `deckhand decide apply NONCE` once answered. Never mark an option
  recommended; the person may answer a hand's question in their own words.
- A `claude attach` line, or none: show the item in a dialog with Done and Later. A prompt in a
  hand is the person's to answer, never yours.
- A link, for a spike's answer: give it; the feature skill reads it with the person.

An item the person put off (Later, Back, a pause, a dismissed dialog) or just answered is not asked
again until the person writes something or another item is on top.

A `build order cycle` line is the person's to break in Linear; say it once.

Before planning a feature, run `deckhand captain apply hold`; while held, a spike's answer or prompt
rings its own line: take it, plan on. On its approval, or when the person pauses, run
`deckhand captain apply release`, then read the context.

On `deckhand: the engine stopped`, say so once and give the start command again from
`deckhand engine apply copy`.

On `ready to release`, tell the person the feature's last story merged; the production release is
theirs. Offer `/deckhand:feature` for the next one.
