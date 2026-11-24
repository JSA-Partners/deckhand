from __future__ import annotations

import json

from deckhand import fleet
from deckhand.step import Refusal, hand_role, own_ref, register
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

USAGE = "mail takes REF; context [--json], or apply REF ack SEQ"
ACTIONS = ("ack",)


def _parse(args: list[str]) -> dict:
    found: dict = {"words": [], "json": False}
    for arg in args:
        if arg == "--json":
            found["json"] = True
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if not found["words"]:
        raise Refusal(f"a story REF is required; {USAGE}")
    return found


@register("mail")
class Mail:
    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        ref = parsed["words"][0]
        if hand_role() and ref != own_ref():
            print(f"{ref} is not this hand's REF; mail context takes its own")
            return
        hand = fleet.session_of(ref)
        with LocalState(default_path()) as rows:
            waiting = rows.pending(hand)
        if parsed["json"]:
            shown = [{"seq": m.seq, "kind": m.kind, "body": m.body} for m in waiting]
            print(json.dumps({"hand": hand, "messages": shown}, indent=2))
            return
        if not waiting:
            print("no message waits")
        for message in waiting:
            print(f"{message.seq} {message.kind}")
            print(message.body)

    def apply(self, args: list[str]) -> None:
        words = _parse(args)["words"]
        if len(words) != 3 or words[1] != "ack":
            raise Refusal(USAGE)
        ref, seq = words[0], words[2]
        hand = fleet.session_of(ref)
        with LocalState(default_path()) as rows:
            seqs = {m.seq for m in rows.pending(hand)}
            if not seq.isdigit() or int(seq) not in seqs:
                raise Refusal(f"{seq or 'SEQ'} is not a pending message of {ref}; {USAGE}")
            count = rows.ack(hand, int(seq))
        print(f"done: acknowledged {count}")
