from __future__ import annotations

import json

from deckhand import claims, features, ops, signoff
from deckhand.order import DONE
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep, parse

USAGE = (
    "after takes REF and [--team-key KEY] on both verbs; context [--json],"
    " apply done N --note TEXT or apply person N --why TEXT"
)
WAITS = "waits for the merge"
ACTIONS = {"done": ("--note", "note", signoff.AFTER_DONE), "person": ("--why", "why", signoff.AFTER_PERSON)}


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
        if len(words) != 3 or words[1] not in ACTIONS:
            raise Refusal(f"apply takes REF, done or person, and an item number; {USAGE}")
        flag, key, kind = ACTIONS[words[1]]
        story, records = self._find(parsed)
        claims.owned(story, self.viewer())
        ref, sid = story["identifier"], story["id"]
        if story["state"] != DONE:
            raise Refusal(f"{ref} is not Done; its after-merge items open once it merges")
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
