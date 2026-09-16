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

What is printed above is the whole issue; nothing needs reading again.
Run superpowers:brainstorming from what is printed above, aimed at a story: the design it reaches is
Story, Scope In and Out, and Acceptance Criteria, told plainly, what a user gets and how we will know. Skip the brainstorm's spec file and commit; the issue is the spec. Design the feature across the
project's repositories; a story elsewhere is one outcome. Give `new apply` a
`--title` that reads as the change would in a commit subject: what it does, not what was wanted. Scale it to the
idea: a fix needs a question or two, a feature the whole conversation.

One story: run superpowers:writing-plans into the draft, with an "After the merge" block at the
end of the Plan for anything that can only happen once the code is on main, and run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new apply <draft>`. It boards the story as Draft. When the
source was a parked feature, run `... new apply --stub <its number> <draft>` instead: the feature
becomes the story and keeps its number, its board item, and everything already waiting on it. Then run
`... next context <number>` and carry on from the Review section of
`${CLAUDE_PLUGIN_ROOT}/skills/next/SKILL.md`, speaking as its Speaking section says.

Several outcomes: settle the requirements, propose the split as one bullet per story in dependency
order, each opening with `owner/name:` when it belongs elsewhere, where it is parked for that
repository's session, confirm it in one question, write the split file, and run `... new apply --split <file>`
(with `--from $source` for a parked feature, which makes it the first story, so the file's first
line is a story for this repository). Dispatch one deckhand:author agent per stub on the
line it printed, all at once, then run `... next context N` for each and carry on from that file's
Review section. Park any other feature the conversation produced with `... new apply --park <file> --title "<name>"`,
with `--repo owner/name` for another repository and `--blocks N` for the story here waiting on it.

If a command refuses, fix the rule it names and run it again.
