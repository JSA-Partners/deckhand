from __future__ import annotations

import os
from pathlib import Path

from deckhand import features, git, implplan, ops
from deckhand.credentials import Credentials
from deckhand.features import Phase
from deckhand.linear.connect import connect
from deckhand.records import Record
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = "implplan takes REF and --team-key KEY on both verbs, and on apply --plan PLAN.md --manifest MANIFEST.json"
TITLE = "Implementation plan"
PURPOSE = "implplan"


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


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
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {USAGE}")
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
class ImplPlan:
    def __init__(self, store: Store | None = None, runner: git.Runner = git.run, repo: Path | None = None) -> None:
        self._store = store
        self._runner = runner
        self._repo = repo

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def context(self, args: list[str]) -> None:
        print(USAGE)
        print("manifest JSON fields:")
        for name, meaning in implplan.SHAPE.items():
            print(f"- {name}: {meaning}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        plan, plan_text = _read(parsed.get("plan", ""), "--plan")
        manifest = implplan.parse_manifest(_read(parsed.get("manifest", ""), "--manifest")[1])
        store = self._store_for_run()
        feature = features.open_feature(store, parsed["team_key"])
        if feature is None:
            raise Refusal("no feature is open")
        current = features.phase(feature)
        if current is not Phase.BUILDING:
            raise Refusal(f"{features.describe(feature.project)} is {current.value}, not building; approve it first")
        story = features.story(store, feature, parsed["words"][0])
        repo = self._repo or Path.cwd()
        if self._runner(["git", "rev-parse", "--verify", "--quiet", "origin/HEAD"], repo, None).returncode != 0:
            raise Refusal("origin/HEAD is not set in this clone; run git remote set-head origin --auto")
        base = git.merge_base(repo, "HEAD", "origin/HEAD", self._runner)
        failures = implplan.plan_checks(manifest, set(git.ls_files(repo, base, self._runner)))
        if failures:
            raise Refusal("the plan fails its checks: " + "; ".join(failures))
        ops.ensure_document(store, story["id"], PURPOSE, story["id"], TITLE, plan_text)
        record = Record(PURPOSE, story["id"], implplan.version(plan, manifest), "agent:implplan", manifest)
        ops.ensure_record(store, story["id"], record)
        print(f"done: saved the implementation plan for {story['identifier']}")
