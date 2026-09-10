---
name: new
description: "Turn an idea into a story, or several, through a conversation that ends with the story on the board and its review under way."
arguments: source
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(deckhand:*)
---

# New $source

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new context "$source"`

Run superpowers:brainstorming from what is printed above, aimed at a story: the design it reaches is
Story, Scope In and Out, and Acceptance Criteria, told to the person plainly, what a user gets and
how we will know. Skip the brainstorm's spec file and commit; the issue is the spec. Scale it to the
idea: a fix needs a question or two, a feature the whole conversation.

One story: run superpowers:writing-plans into the draft, with an "After the merge" block at the
end of the Plan for anything that can only happen once the code is on main, and run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new apply <draft>`. It boards the story as Draft. Then run
`... next context <number>` and carry on from the Review section of
`${CLAUDE_PLUGIN_ROOT}/skills/next/SKILL.md`, speaking as its Speaking section says.

Several outcomes: settle the requirements, propose the split as one bullet per story in dependency
order, confirm it in one question, write the split file, and run `... new apply --split <file>`
(with `--from $source` for a parked feature). Dispatch one deckhand:author agent per stub on the
line it printed, all at once, then run `... next context N` for each and carry on from that file's
Review section. Park any other feature the conversation produced with `... new apply --park <file>`.

If a command refuses, fix the rule it names and run it again.
