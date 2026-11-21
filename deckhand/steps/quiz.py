from __future__ import annotations

import json
from pathlib import Path

from deckhand import claims, git, implplan, inputs, ops, quiz, summary, tiers, tuicr, versions
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import DEFAULT_BASE, Located, StoryStep
from deckhand.steps.signoff import review_hunks
from deckhand.summary import ChangeSummary, digest, parse_summary

USAGE = (
    "quiz takes REF and [--team-key KEY] on both verbs; context [--json], apply --quiz QUIZ.json --summary SUMMARY.json"
)
SHAPE = 'a JSON list of {"question", "options": [2 to 4 strings], "correct": index, "where": "path:line"}'


def _parse(args: list[str]) -> dict:
    found: dict = {"words": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--quiz", "--summary"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if len(found["words"]) != 1:
        raise Refusal(f"quiz takes one story REF; {USAGE}")
    return found


def _read(path: str, flag: str) -> str:
    return inputs.read(path, flag, USAGE)


class Quiz(StoryStep):
    def _worktree(self, at: Located) -> tuple[dict, list[Record], tiers.Tier | None, str, Path]:
        if at.path is None or at.head is None:
            ref = at.story["identifier"]
            raise Refusal(f"{ref} has no worktree; run build apply {ref} worktree")
        held = versions.held(at.records, "tier", at.story["id"])
        tier = tiers.from_record(held.data) if held else None
        return at.story, at.records, tier, at.head, at.path

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        story, records, tier, head, path = self._worktree(self.locate(parsed["words"][0], team_key(parsed)))
        held = quiz.held_set(records, story["id"])
        facts = {
            "story": story["identifier"],
            "worktree": str(path),
            "head": head,
            "tier": tier.level if tier else None,
            "base": quiz.BASE[tier.level] if tier else None,
            "shape": SHAPE,
            "summary_shape": summary.SHAPE,
            "quiz_head": held.data.get("head") if held else None,
            "open": quiz.open_items(records, story["id"]) if held else None,
        }
        if "--json" in args:
            print(json.dumps(facts, indent=2))
            return
        print(f"quiz for {facts['story']} in {facts['worktree']} at {head}")
        if tier is None:
            print("no tier decided; the quiz waits for one")
        else:
            print(f"size: {facts['base']} for tier {tier.level}, plus one per new_here entry in the change summary")
        print(f"shape: {SHAPE}")
        print("change summary, a JSON object:")
        for name, meaning in summary.SHAPE.items():
            print(f"- {name}: {meaning}")
        if held is None:
            print("no quiz saved")
            return
        left = ", ".join(str(n) for n in facts["open"]) or "none"
        print(f"saved quiz at {facts['quiz_head']}; items still open: {left}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        at = self.locate(parsed["words"][0], team_key(parsed))
        story, _, tier, head, path = self._worktree(at)
        claims.owned(story, self.viewer())
        if tier is None:
            raise Refusal(f"{story['identifier']} has no tier decided; the quiz is sized by its tier")
        text, summary_text = _read(parsed.get("quiz", ""), "--quiz"), _read(parsed.get("summary", ""), "--summary")
        parsed_summary = parse_summary(summary_text)
        new_here = len(parsed_summary.new_here)
        items = quiz.parse_quiz(text, quiz.quiz_size(tier, new_here))
        if git.dirty(path, self._runner):
            raise Refusal(f"{path} has uncommitted changes; commit them first so the quiz is pinned to {head}")
        ranges = self._shown(at, path, parsed_summary)
        for n, entry in enumerate(items, start=1):
            where, _, line = entry.where.rpartition(":")
            if ranges and not tuicr.shows(ranges, where, int(line)):
                raise Refusal(
                    f"quiz item {n} asks about {entry.where}, which the tuicr review does not show;"
                    f" ask about {tuicr.describe_ranges(ranges)}"
                )
        record = quiz.set_record(story["id"], head, items, digest(parsed_summary), new_here)
        ops.ensure_record(self._store_for_run(), story["id"], record)
        print(f"done: saved a quiz of {len(items)} items for {story['identifier']} at {head}")

    # The person is quizzed only on code the tuicr review shows them, or on the whole diff when it shows none.
    def _shown(self, at: Located, path: Path, summary: ChangeSummary) -> list[tuple[str, int, int]]:
        plan = versions.held(at.records, implplan.PURPOSE, at.story["id"])
        manifest = plan.data if plan else {"contract_sites": [], "invariant_sites": []}
        _, hunks = review_hunks(at, path, summary, manifest, self._runner)
        if not hunks:
            base = git.merge_base(path, "HEAD", DEFAULT_BASE, self._runner)
            hunks = tuicr.parse_hunks(git.diff(path, base, runner=self._runner))
        return tuicr.shown_ranges(hunks)


register("quiz")(Quiz)
