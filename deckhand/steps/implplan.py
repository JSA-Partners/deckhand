from __future__ import annotations

from pathlib import Path

from deckhand import answers, git, implplan, ops, review
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep

USAGE = "implplan takes REF and [--team-key KEY] on both verbs, and on apply --plan PLAN.md --manifest MANIFEST.json"
TITLE = "Implementation plan"


def _parse(args: list[str]) -> dict:
    found: dict = {"words": []}
    rest = iter(args)
    for arg in rest:
        if arg in ("--team-key", "--plan", "--manifest"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if len(found["words"]) != 1:
        raise Refusal(f"implplan takes one story REF; {USAGE}")
    return found


def _read(path: str, flag: str) -> tuple[bytes, str]:
    if not path:
        raise Refusal(f"{flag} is required; {USAGE}")
    try:
        data = Path(path).read_bytes()
        return data, data.decode()
    except OSError:
        raise Refusal(f"{path}: cannot be read") from None
    except UnicodeDecodeError:
        raise Refusal(f"{path}: not valid UTF-8") from None


@register("implplan")
class ImplPlan(StoryStep):
    def context(self, args: list[str]) -> None:
        print(USAGE)
        print("manifest JSON fields:")
        for name, meaning in implplan.SHAPE.items():
            print(f"- {name}: {meaning}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        plan, plan_text = _read(parsed.get("plan", ""), "--plan")
        manifest = implplan.parse_manifest(_read(parsed.get("manifest", ""), "--manifest")[1])
        at = self.locate(parsed["words"][0], team_key(parsed))
        self.owned(at)
        story, store, repo = at.story, self._store_for_run(), at.path or at.clone
        if self._runner(["git", "rev-parse", "--verify", "--quiet", "origin/HEAD"], repo, None).returncode != 0:
            raise Refusal("origin/HEAD is not set in this clone; run git remote set-head origin --auto")
        base = git.merge_base(repo, "HEAD", "origin/HEAD", self._runner)
        failures = implplan.plan_checks(manifest, set(git.ls_files(repo, base, self._runner)))
        if failures:
            raise Refusal("the plan fails its checks: " + "; ".join(failures))
        ops.ensure_document(store, story["id"], implplan.PURPOSE, story["id"], TITLE, plan_text)
        record = Record(implplan.PURPOSE, story["id"], implplan.version(plan, manifest), "agent:implplan", manifest)
        ops.ensure_record(store, story["id"], record)
        review.save_text(answers.state_dir(), story["id"], record.version, plan_text)
        print(f"done: saved the implementation plan for {story['identifier']}")
