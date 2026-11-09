---
name: new
description: "Turn an idea into a story, or several, through a conversation that ends with the story on the board and its review under way."
arguments: source
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(deckhand:*)
---

# New $source

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new context --idea "$source"`

What is printed above is the whole request; nothing needs reading again. When it says an issue
number is next's, say so in one sentence and stop.
Run superpowers:brainstorming from what is printed above, aimed at a story: the design it reaches is
Story, Scope In and Out, and Acceptance Criteria, told plainly, what a user gets and how we will know. Skip the brainstorm's spec file and commit; the issue is the spec. Design the feature across the
project's repositories; a story elsewhere is one outcome. Give `new apply` a
`--title` that reads as the change would in a commit subject: what it does, not what was wanted. Scale it to the
idea: a fix needs a question or two, a feature the whole conversation.

One story: run superpowers:writing-plans into the draft, with a `### After the merge`
heading at the end of the Plan for anything that can only happen once the code is on main (an item
for another, merged story goes on its list with `after apply N --add "<item>"`, never a hand edit). Where any
part changes what a person sees (a screen, a component, a layout, styling, wording on screen), the plan
names the impeccable:impeccable steps for it; lean toward yes. Run
`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new apply <file> --feature "<name>"`. It boards the story as Draft. Then run
`... next context <number>` and carry on from the Review section of
`${CLAUDE_PLUGIN_ROOT}/skills/next/SKILL.md`, speaking as its Speaking section says.

Several outcomes: settle the requirements, propose the split as one bullet per story in dependency
order, each opening with `owner/name:` when it belongs elsewhere, where it is parked for that
repository's session, confirm it in one question, write the split file, and run
`... new apply --split <file>`. Dispatch one deckhand:author agent per draft on the
line it printed, all at once, then run `... next context N` for each and carry on from that file's
Review section. Park any other feature the conversation produced with `... new apply --park <file> --title "<name>"`,
with `--repo owner/name` for another repository, `--blocks N` for the story here waiting on it, and
`--after REF` for a story the feature itself waits on.

Every story goes into a feature: give `new apply` a `--feature` from the Features the context
prints, or let it inherit the one of the story it was parked or split from. Name the one you chose.

If a command refuses, fix the rule it names and run it again.
