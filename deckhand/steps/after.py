from __future__ import annotations

import json

from deckhand import claims, features, git, ops, signoff
from deckhand.order import DONE
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep, branch_of, parse, require_base, sync

USAGE = (
    "after takes REF and [--team-key KEY] on both verbs; context [--json],"
    " apply done N --note TEXT, apply person N --why TEXT, or apply sync"
)
WAITS = "waits for the merge"
ITEMS = {"done": ("--note", "note", signoff.AFTER_DONE), "person": ("--why", "why", signoff.AFTER_PERSON)}
SYNC = "sync"
ACTIONS = (*ITEMS, SYNC)


def _arguments(args: list[str]) -> dict:
    rest, flags = [], {"note": "", "why": ""}
    it = iter(args)
    for arg in it:
        if arg in ("--note", "--why"):
            flags[arg[2:]] = next(it, "")
        else:
            rest.append(arg)
    return parse(rest, USAGE) | flags


def _item(story: dict, items: list[str], word: str) -> int:
    if not word.isdigit() or int(word) < 1:
        raise Refusal(f"an after-merge item is a number from 1; {USAGE}")
    if int(word) > len(items):
        raise Refusal(f"{story['identifier']} has no item {word}; it has {len(items)}")
    return int(word)


@register("after")
class After(StoryStep):
    def _find(self, parsed: dict) -> tuple[dict, list[Record]]:
        store = self._store_for_run()
        feature = features.owning(store, team_key(parsed), parsed["words"][0])
        story = features.story(store, feature, parsed["words"][0])
        return story, feature.records.get(story["id"], [])

    def context(self, args: list[str]) -> None:
        parsed = _arguments(args)
        if len(parsed["words"]) != 1:
            raise Refusal(f"context takes one story REF; {USAGE}")
        story, records = self._find(parsed)
        sid, done = story["id"], story["state"] == DONE
        items = []
        for n, text in enumerate(signoff.after_items(records, sid), start=1):
            status, held = signoff.after_status(records, sid, n, text)
            noted = {key: held.data[key] for key in ("note", "why") if held is not None and key in held.data}
            items.append({"n": n, "text": text, "status": status if done else WAITS} | noted)
        if parsed["json"]:
            print(json.dumps({"story": story["identifier"], "done": done, "items": items}, indent=2))
            return
        ref = story["identifier"]
        print(f"after-merge for {ref}" + ("" if done else f": {ref} is not Done"))
        if not items:
            print("no after-merge items")
        for item in items:
            extra = item.get("note") or item.get("why")
            print(f"{item['n']} {item['text']}: {item['status']}" + (f" ({extra})" if extra else ""))

    def apply(self, args: list[str]) -> None:
        parsed = _arguments(args)
        words = parsed["words"]
        if words[1:] == [SYNC]:
            self._sync(parsed)
            return
        if len(words) != 3 or words[1] not in ITEMS:
            raise Refusal(f"apply takes REF, done or person, and an item number; {USAGE}")
        flag, key, kind = ITEMS[words[1]]
        story, records = self._merged(parsed)
        ref, sid = story["identifier"], story["id"]
        items = signoff.after_items(records, sid)
        n = _item(story, items, words[2])
        status, _ = signoff.after_status(records, sid, n, items[n - 1])
        if status != signoff.OPEN:
            raise Refusal(f"item {n} of {ref} is {status}")
        text = parsed[key].strip()
        if not text:
            raise Refusal(f"{flag} TEXT is required; {USAGE}")
        record = signoff.after_record(kind, sid, n, items[n - 1], {key: text})
        ops.ensure_record(self._store_for_run(), sid, record)
        print(f"done: item {n} of {ref} is {signoff.after_status([*records, record], sid, n, items[n - 1])[0]}")

    def _merged(self, parsed: dict) -> tuple[dict, list[Record]]:
        story, records = self._find(parsed)
        claims.owned(story, self.viewer())
        if story["state"] != DONE:
            raise Refusal(f"{story['identifier']} is not Done; its after-merge items open once it merges")
        return story, records

    # The sandbox lets a hand write neither FETCH_HEAD nor the worktree's git metadata, so main comes through here.
    def _sync(self, parsed: dict) -> None:
        story, records = self._merged(parsed)
        ref, clone, branch = story["identifier"], self._clone(story["labels"]), branch_of(records, story)
        path = git.worktree_path(clone, branch) if branch else None
        if path is None or not path.is_dir():
            raise Refusal(f"{ref} has no worktree to bring main into")
        require_base(clone, self._runner)
        sync(
            path,
            ref,
            self._runner,
            f"leave the item that needs main to the person with after apply {ref} person N --why TEXT",
        )
