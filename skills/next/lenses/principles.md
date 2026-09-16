---
name: principles
always: true
signals: [refactor, duplicate, shared, helper, util, abstract, generic, interface, config, flag, option]
---

# Principles

Read the plan for what it builds that the story does not need, what it builds twice, and what it bolts onto something that should have been split. Three questions per task: is this required by an acceptance criterion; does the repository already have it; does this task have one reason to change.

A finding names the task, quotes the step, and says which of the three questions it fails. Suggest the smaller shape. Do not propose abstractions the story does not need; the rule of three cuts both ways.

Evidence is the plan step plus, where the duplication is real, the `file:line` of the existing code.

Ask whether the story delivers one outcome or several: a body that names two things a user gets,
or a plan whose tasks serve different criteria, is more than one story. Say so as a finding named
"more than one story" with the split you would make, one line per story.

Nothing found is a valid result. Say so in one line.
