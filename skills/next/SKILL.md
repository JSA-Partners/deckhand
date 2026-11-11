---
name: next
description: "Carry a story on from wherever it is, through every step your answers allow, stopping where you decide."
arguments: issue
argument-hint: "<issue-number>"
effort: high
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/deckhand" *) Bash(git *) Bash(gh pr merge *) Bash(gh run view *) Read Write Edit Grep Glob Agent AskUserQuestion EnterWorktree ExitWorktree Skill(superpowers:*) Skill(deckhand:*)
---

# Next $issue

!`"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" next context $issue`

If no number was given, ask which story. Say where the story is in two sentences from the briefing
above, then run the step it names and carry on to the next one until a
decision is the person's, or you are unsure, and ask. Every command below
runs as `"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" <command>`; when a step needs a context the briefing
did not print, run `<step> context $issue`. A `Plan:` or `Body:` line names a file holding that
text: read the file, not the context again. When a command refuses, fix the rule it names
and rerun it, or say in one sentence why you cannot. When the briefing or a command prints a
`Worktree:` path that is not this directory, enter it with EnterWorktree first; in a worktree entered for the first time, run the repository's setup commands from its
CLAUDE.md or README, saying one line while they run; no test run, main's checks are the
baseline; start copies the ignored files `.worktreeinclude` names; when setup or a test fails for
another the clone has, copy it once and say it belongs there. On the after or done row in a worktree,
leave it with ExitWorktree, keep, before anything. Done and stop: say what the briefing says. Every commit goes through deckhand:commit, which never skips a hook;
nothing is amended past the last reviewed commit or pushed by hand.

## Speaking

Talk to the person as to a colleague from another project, in plain words. Say one line before anything slow. Recommend every time you ask.
When the run ends because the story waits on something outside this session, say where it is,
the one link worth opening, and what happens next. Never show what a
command printed; say what matters. A failure is one sentence on what could not be done.

When they approve something, tell the story plainly first: what a user gets when it is done, how we
will know, and the work as numbered steps of one sentence. The technical body stays on the issue.

Park a feature this work turns up for a later story with
`new apply --park <file> --title "<name>" --from $issue`, adding `--after $issue` when it waits on
this one, and name the number it prints. Fold or drop a story only with
`captain apply --withdraw REF --note "<why>"`.

## Write

A draft: dispatch the deckhand:author agent with the number and the path to deckhand, say what it
wrote, and carry on to the review.

## Settle

A parked feature: run superpowers:brainstorming with them from the context above, and its Notes,
until the requirements say what a user gets, what is in and out, and how we will know it is done.
One outcome: dispatch deckhand:author as for a draft. Several: propose the split as one story per line
in dependency order, each opening with `owner/name:` when it belongs elsewhere, confirm it in one
question, write the split file, run `new apply --split <file> --from $issue`, and dispatch one
author per draft it prints, all at once, then review each. Park each feature
the brainstorm turns up as in Speaking, with `--repo owner/name` for another repository, `--after
REF` for any story it waits on, and `--blocks $issue` in place of `--from` when this story waits
on it.

## Review

The context names three draft paths: findings, verdicts, decisions. Run the deckhand:reviewer
agent with the body and brief file paths and write its lines as they are to the findings file.
If it found something, run the deckhand:skeptic agent with those lines and the body file's path and
write its lines to the verdicts file. Then speak: a verdict on the whole story, sound, needs
amending, flawed, or more than one story, and why; a flawed story is rewritten and reviewed again before anything else is asked; a
story that is more than one gets the split proposed as `new` does; on yes the rest go out with `new apply --park <file> --repo owner/name --from $issue`, adding
`--after` as the split says, or `--blocks $issue` in place of `--from`, this story is narrowed with a plain amend, and the review runs again. Then the findings
in three groups, what changes what the story delivers, what changes how it is built, and the small
ones, each as a sentence on the problem and one on accepting it. Decide the clear ones with the
skeptic: accept a confirmed finding that improves the story, decline what it rejected, say each
in a sentence, and ask only where one changes what the story delivers. When the context prints a
Budget line, the decisions say what the plan drops or splits out. Write them to the
decisions file and run
`review apply $issue <findings> <verdicts> <decisions> --verdict "<sentence>"`; on a clean pass,
the findings file alone with `--verdict`. Then amend from the accepted findings, with `--minor`,
which keeps it in Ready, when every confirmed finding is P3 or the context says a last review is
in the brief and none is P1, and carry on to Board.

## Amend

From the amend context (above, or run it), copy the body file it names to the draft it names and
edit that, keeping every heading, with superpowers:writing-plans for a plan rewrite, and run
`amend apply $issue <file> --note "<what changed and why>"`, never where it came from, with
`--title "<new title>"` when the Story no longer matches
it. An amend writes Refinement, so the next briefing asks for the review again, unless `--minor`
says it needs no fresh look; say what changed and carry on. Work that belongs in its own story: overwrite the draft with its whole body
and run `amend apply $issue <file> --new-issue "<title>"`; work that must land first takes `--before`.

## Board

From the ready context (above, or run it), brief the story in three sentences: what it changes, as
behavior rather than files; what it leaves alone, from Scope Out; and the one judgment call the
plan makes, with the alternative named. With none, the third sentence says so. Propose kind, and points by
naming the reference story it is most like and why, and ask one question about that call: "The
plan does X rather than Y; board it that way, review it again, or not yet?" A plan that waits on other stories
boards with them as blockers.
Say the kind and points before boarding. Board it:
`ready apply $issue --kind K --points P`, with `--blocked-by M` per open blocker, `owner/name#M` for
one elsewhere, then carry on to the check. Review it again: the Review section. Not yet: stop.

## Wait

The story waits on what the briefing lists: say so in one sentence and stop.

## Check and build

From the start context (above, or run it), compare the plan with the code and with what landed on
main since the review; say what you found and recommend build, amend, or review again. When they say build, run
`start apply $issue --note "<the check's conclusion>"`; it prints the story's worktree, where the
build runs. Then superpowers:subagent-driven-development on the plan, with impeccable:impeccable
where the Visual work line applies, using the least
powerful model that can do the task, one commit per task with
deckhand:commit, nothing pushed; after each commit run `update apply $issue`, and on a conflict
resolve it, commit, and log a Deviation. A change that serves the
criteria as written: build it and run `log $issue "Deviation: <what and why>"`. A change that
alters what the story delivers: say so in one sentence with the diff's size and a recommendation,
here or a new story, and log the answer as a Deviation naming the criterion, or split it with the
amend step. A decision that changes an issue in another repository before it starts: run
`amend context N --repo owner/name`, edit its draft, run
`amend apply N <file> --repo owner/name --note "<why>"`, and name it in the Deviation. An item for
another merged story's list: `after apply N --add "<item>"`, never a hand edit. When it says resume, say which tasks the commits cover and recommend carrying on,
which skips them, while tasks are left, or reviewing what is there. When the briefing lists `Blocked by:`, say what it waits on, build what does not
depend on it, then stop. A story that must not start or merge before another: run
`captain apply --block $issue --by M`, run `log $issue "Noted: <why>"`, and stop.

## Branch review

When the plan is done, run one superpowers code-review subagent over `origin/main..HEAD` with the
Body file and Deviations from `finish context` as its brief, reporting P1 to P3 with file and
line. Fix every finding worth taking through deckhand:commit: P1 and P2 always, a P3 when it is
idiomatic and matches the patterns already in the file. Then say in a sentence what the review found
and fixed, naming only a finding you are unsure of. Then walk the
change one group of files at a time, in reading order: what it does and why, a short
snippet, the criterion it serves, and any Deviation it carries; wait for them before the next.
After the last, give them
`cd "<the worktree>" && tuicr -r origin/main..HEAD` as a plain message;
they paste the export or say there are no comments. On comments: fix every
one, commit with deckhand:commit, run the same review over
`<the previous pass's last commit>..HEAD` only, fix its P1 and P2, and hand off the same way. The review never runs on its own fixes and never after a clean pass. Record the clean pass with `reviewed apply $issue "<one line>"`; the pull
request opens only from that commit. Then run the
deckhand:document skill, fixing what its audit lists first; its commits land past the reviewed one
and need no pass.

## Pull request

From the finish context (above, or run it), write the summary it names under its Summary rule, in
the third person, the story's "so that" as the why, folding in only the Deviations that still
hold. Read them the title and that summary, and on yes run
`finish apply $issue <summary> --check "<cmd>"`, the check commands from CLAUDE.md or the detected list, adding
`--breaking "<text>"` when a client must react. Say where the pull request is and that merging is
theirs, on GitHub or by saying merge here (`gh pr merge --squash`). Take no word for the
state: when they come back, run `next context $issue` and act on what it says. On the merge row,
say which checks are still running when the briefing names them, and make the same offer. On the
update row, run `update apply $issue`, say main moved on and the checks run again, and carry on;
when it refuses for a conflict, do what it says and log a Deviation.

## Fix

On the fix row a check failed after the pull request opened. Read the failed job with
`gh run view --log`, say the cause in a sentence, fix it on the branch through deckhand:commit,
and log a Deviation. Then the Branch review section from the last reviewed commit, and the Pull
request section again: finish pushes and keeps the pull request.

## After the merge

On a closed story, run `after apply $issue` first, which sets the column from the boxes:
Verification while any is left, Done when none is. Then walk the items one at a time, doing what
can be done here and asking for what is theirs; park one that is a change of its own as above,
adding `--repo owner/name`. An item that cannot be done where a merge deploys is parked, then dropped with
`after apply $issue --drop <n> --note "<where>"`. Run `after apply $issue --item <n>` as each is done, ticking its box.
When nothing is left, say the story is finished and name the next story the briefing gave.
Read the briefing's `Clear:` line out either way.
With no story in hand, `/deckhand:captain` says what to pick up next across every repository.
