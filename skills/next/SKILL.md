---
name: next
description: "Carry a story on from wherever it is, through every step your answers allow, stopping where you decide."
arguments: issue
argument-hint: "<issue-number>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Bash(gh pr merge *) Read Write Edit Grep Glob Agent AskUserQuestion Skill(superpowers:*) Skill(deckhand:*)
---

# Next $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" next context $issue`

If no number was given, ask which story. Say where the story is in two sentences from the briefing
above, then run the step it names and carry on to the next step in the same conversation until a
decision is the person's, or until you are unsure what they would want, and ask. Every command below
runs as `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" <command>`; when a step needs a context the briefing
did not print, run `<step> context $issue` yourself. When a command refuses, fix the rule it names
and run it again; when you cannot, say so in one sentence. Done and stop have nothing to run: say
what the briefing says, in the Speaking rules' shape.

## Speaking

Talk to the person as to a colleague from another project. Say one line when something will take
more than a moment. Ask only when a decision is theirs or you are unsure, with a recommendation every
time. When the run ends because the story waits on something outside this session, say where it is,
the one link worth opening, and what happens next, in two or three sentences. Never show what a
command printed; read it and say what matters. A failure is one sentence on what could not be done.

When they approve something, tell the story plainly first: what a user gets when it is done, how we
will know, and the work as numbered steps of one sentence. The technical body stays on the issue;
give it or explain any line on request.

## Write

A stub: dispatch the deckhand:author agent with the number and the path to deckhand, say in a
sentence what it wrote, and carry on to the review.

## Review

The context names three draft paths: findings, verdicts, decisions. Run the deckhand:reviewer
agent with the body and the brief and write its lines as they are to the findings file. If it found
something, run the deckhand:skeptic agent with those lines and the body and write its lines to the
verdicts file. Then speak: a verdict on the story as a whole, sound, needs amending, or flawed, and
why; a flawed story is rewritten and reviewed again before anything else is asked. Then the findings
in three groups, what changes what the story delivers, what changes how it is built, and the small
ones, each as a sentence on the problem and one on what accepting it would do. Take the decisions in
conversation, write them to the decisions file, and run
`review apply $issue <findings> <verdicts> <decisions> --verdict "<sentence>"`; on a clean pass,
the findings file alone with `--verdict`. Then amend from the accepted findings.

## Amend

From the amend context (above, or run it), edit the body into the draft keeping every heading, with
superpowers:writing-plans for a plan rewrite, and run
`amend apply $issue <draft> --note "<what changed and why>"`, with `--title "<new title>"` when
the Story no longer matches it. If the amend changed what the story delivers, recommend reviewing
again and ask; otherwise carry on to the board. When the briefing's step is reconsider, that
question is the step. Work that belongs in its own story: overwrite the draft with its whole body
and run `amend apply $issue <draft> --new-issue "<title>"`.

## Board

From the ready context (above, or run it), tell the story plainly, propose kind and points from
the done stories that most resemble it, and ask one question: board it, review it again, or not
yet. Board it: `ready apply $issue --kind K --points P`, with `--blocked-by M` per open blocker,
then carry on to the check. Review it again: the Review section. Not yet: stop.

## Check and build

From the start context (above, or run it), compare the plan with the code and with what landed on
main since the review; say what you found and recommend build, amend, or review again. When they say build, run
`start apply $issue --note "<the check's conclusion>"`, then superpowers:subagent-driven-development
on the plan, one commit per task with deckhand:commit, nothing pushed. A change that serves the
criteria as written: build it and run `log $issue "Deviation: <what and why>"`. A change that
alters what the story delivers: say so in one sentence with the diff's size and a recommendation,
here or a new story, and log the answer as a Deviation naming the criterion, or split it with the
amend step. Ask when unsure. When the briefing says build, the check is done: check the branch out
and run the plan. When it says resume, say which tasks the commits cover and ask whether to carry
on or review what is there, recommending carry on while tasks are left; carrying on skips the tasks
the commits cover.

## Branch review

When the plan is done, say so and give them `tuicr -r origin/main..HEAD` as a plain message they
can copy, to run in a terminal of their own; they paste the export or say there are no comments.
Fix every comment, commit with deckhand:commit, and give them
`tuicr -r <last reviewed commit>..HEAD`, the HEAD the previous pass read, for the new commits,
until a pass has no comments. Log the clean pass with `log $issue "Reviewed: <full sha> <one
line>"`, the full sha first; the pull request opens only from that commit. Then run the
deckhand:document skill.

## Pull request

From the finish context (above, or run it), read them the title and body, and on yes run
`finish apply $issue --actual P --check "<cmd>"`, Actual in the estimate's units and proposed from
it, the check commands from CLAUDE.md or the detected list, adding `--breaking "<text>"` when a
client must react. Say where the pull request is and that merging is theirs, on GitHub or by saying
merge here, which runs `gh pr merge --squash`. On the merge row, make the same offer.

## After the merge

On a merged story with items left, walk them one at a time, doing what can be done here and asking
for what is theirs, logging `After the merge: <item>` as each is done. When nothing is left, say the
story is finished and name the next story the briefing gave.
