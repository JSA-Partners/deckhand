---
name: new
description: "Turn a request into stories: brainstorm one, split a feature into stubs, or write a stub into a story with its plan."
arguments: source
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Read Write Edit Grep Glob Agent AskUserQuestion
---

# New $source

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new context "$source"`

Run superpowers:brainstorming from what is printed above. If it is a stub, brainstorm that story's
scope against the requirements and its siblings, run superpowers:writing-plans into the draft, and
run `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" new apply --stub $source <draft>`. If it fits one story,
write it with its plan and run `... new apply <draft>`. If it is more than one story, settle the
requirements, write the split file with one bullet per story in dependency order, confirm the list
with the user in one question, and run `... new apply --split <file>` (add `--from $source` for a
parked feature), then dispatch one deckhand:author agent per stub on the line it printed, all at
once, and relay what each returned; park any other feature the discussion produced with
`... new apply --park <file>`. Say what each command printed; if one refuses, fix what it names and
run it again.
