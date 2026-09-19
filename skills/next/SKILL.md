---
name: next
description: "Carry a story on from wherever it is, through every step your answers allow, stopping where you decide."
arguments: issue
argument-hint: "<issue-number>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Bash(gh pr merge *) Bash(gh run view *) Read Write Edit Grep Glob Agent AskUserQuestion EnterWorktree Skill(superpowers:*) Skill(deckhand:*)
---

# Next $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" next context $issue`

If no number was given, ask which story. Say where the story is in two sentences from the briefing
above, then run the step it names and carry on to the next step in the same conversation until a
decision is the person's, or until you are unsure what they want, and ask. Every command below
runs as `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" <command>`; when a step needs a context the briefing
did not print, run `<step> context $issue`. When a command refuses, fix the rule it names
and run it again; when you cannot, say so in one sentence. When the briefing or a command prints a
`Worktree:` path that is not this directory, enter it with the EnterWorktree tool before anything
else; in a worktree entered for the first time, run the repository's setup commands from its
CLAUDE.md or README, saying one line while they run; no test run, main's checks are the
baseline; when setup or a test there fails for a file the clone has and git ignores, copy it from
the clone once and say so. Done and stop have nothing to run: say what the briefing says. Every commit goes through deckhand:commit, which never skips a hook; nothing is
amended past the last reviewed commit or pushed by hand.

## Speaking

Talk to the person as to a colleague from another project, in plain words, an analogy only when
they ask. Say one line when something will take
more than a moment. Recommend every time you ask.
When the run ends because the story waits on something outside this session, say where it is,
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
verdicts file. Then speak: a verdict on the story as a whole, sound, needs amending, flawed, or more than one
story, and why; a flawed story is rewritten and reviewed again before anything else is asked; a
story that is more than one gets the split proposed as `new` does; on yes the rest go out with `new apply --park <file> --repo owner/name`, blocked
as the split says, this story is narrowed with a plain amend, and the review runs again. Then the findings
in three groups, what changes what the story delivers, what changes how it is built, and the small
ones, each as a sentence on the problem and one on what accepting it would do. Take the decisions in
conversation, write them to the decisions file, and run
`review apply $issue <findings> <verdicts> <decisions> --verdict "<sentence>"`; on a clean pass,
the findings file alone with `--verdict`. Then amend from the accepted findings.

## Amend

From the amend context (above, or run it), edit the body into the draft it names, keeping every heading, with
superpowers:writing-plans for a plan rewrite, and run
`amend apply $issue <draft> --note "<what changed and why>"`, the note saying what changed and
why and never where it came from, with `--title "<new title>"` when the Story no longer matches
it. If the amend changed what the story delivers, recommend reviewing
again and ask; otherwise carry on to the board. When the briefing's step is reconsider, that
question is the step. Work that belongs in its own story: overwrite the draft with its whole body
and run `amend apply $issue <draft> --new-issue "<title>"`; work that must land first takes `--before`.

## Board

From the ready context (above, or run it), tell the story plainly, propose kind, and points by naming
the reference story it is most like and why, and ask one question: board it, review it again, or not
yet. A story whose plan waits on other stories
boards with them as blockers rather than waiting unboarded.
Say the kind and the points in the sentence before boarding, even when the answer came early. Board it: `ready apply $issue --kind K --points P`, with `--blocked-by M` per open blocker,
then carry on to the check. Review it again: the Review section. Not yet: stop.

## Check and build

From the start context (above, or run it), compare the plan with the code and with what landed on
main since the review; say what you found and recommend build, amend, or review again. When they say build, run
`start apply $issue --note "<the check's conclusion>"`; it prints the story's worktree, where the
build runs. Then superpowers:subagent-driven-development on the plan, using the least
powerful model that can do the task, one commit per task with
deckhand:commit, nothing pushed; after each commit run `update apply $issue`, and on a conflict
resolve it, commit, and log a Deviation. A change that serves the
criteria as written: build it and run `log $issue "Deviation: <what and why>"`. A change that
alters what the story delivers: say so in one sentence with the diff's size and a recommendation,
here or a new story, and log the answer as a Deviation naming the criterion, or split it with the
amend step. When the briefing says build, the check is done: run the plan. When it says resume, say which tasks the commits cover and ask whether to carry
on or review what is there, recommending carry on while tasks are left, which skips the tasks the
commits cover. When the briefing lists `Blocked by:`, say what it waits on, build what
does not depend on it, then stop rather than watch. The captain picks it up when the blocker clears.

## Branch review

When the plan is done, run one superpowers code-review subagent over `origin/main..HEAD` with the
story, plan, and Deviations from `finish context` as its brief, reporting P1 to P3 with file and
line. Fix P1 and P2 through deckhand:commit and keep the P3s. Then say what the review found and
fixed in a sentence, list the P3s one line each for them to take or leave, and give them
`cd "<the worktree>" && tuicr -r origin/main..HEAD` as a plain message for a terminal of their own;
they paste the export or say there are no comments. On comments: fix every
one, commit with deckhand:commit, run the same review over
`<the previous pass's last commit>..HEAD` only, fix its P1 and P2, and hand off the same way with
that range. The review never runs on its own fixes and never after a clean pass; only their
comments start a round. Log the clean pass with `log $issue "Reviewed: <full sha> <one line>"`, the
full sha first; the pull request opens only from that commit. Then run the
deckhand:document skill, fixing what its audit lists first; its commits land past the reviewed one
and need no pass, because docs/claude is Claude's alone.

## Pull request

From the finish context (above, or run it), write the summary it names: one or two paragraphs in
the third person, never "I" or "we", saying what the branch changed and what it means for a reader,
in the shape of the repository's recent merged pull requests. The story's "so that" clause has the why. Read them the title and that summary, and on yes run
`finish apply $issue <summary> --check "<cmd>"`, the check commands from CLAUDE.md or the detected list, adding
`--breaking "<text>"` when a client must react. Say where the pull request is and that merging is
theirs, on GitHub or by saying merge here, which runs `gh pr merge --squash`. On the merge row,
say which checks are still running when the briefing names them, and make the same offer. On the
update row, run `update apply $issue`, say main moved on and the checks run again, and carry on;
when it refuses for a conflict, do what it says and log a Deviation.

## Fix

On the fix row a check failed after the pull request opened. Read the failed job with
`gh run view --log`, say the cause in a sentence, fix it on the branch through deckhand:commit,
and log a Deviation. Then the Branch review section from the last reviewed commit, and the Pull
request section again: finish pushes and keeps the pull request.

## After the merge

On a merged story with items left, walk them one at a time, doing what can be done here and asking
for what is theirs, logging `After the merge: <item>` as each is done, or deferred with where it went, so the story
closes. When nothing is left, say the story is finished and name the next story the briefing gave; when the briefing says the worktree is
here, add that its folder goes away on the next run from the clone.
With no story in hand, `/deckhand:captain` says what to pick up next across every repository.
