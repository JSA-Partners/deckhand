from __future__ import annotations

import sys

from deckhand.step import REGISTRY, VERBS, Refusal


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in REGISTRY:
        name = argv[0] if argv else ""
        print(f"deckhand: unknown command {name!r}", file=sys.stderr)
        return 2
    if len(argv) < 2 or argv[1] not in VERBS:
        print(f"deckhand: {argv[0]} takes one of {', '.join(VERBS)}", file=sys.stderr)
        return 2
    command, verb, args = REGISTRY[argv[0]], argv[1], argv[2:]
    if verb == "context":
        try:
            getattr(command, verb)(args)
        except Exception as problem:
            print(f"deckhand: {argv[0]} context could not be read: {problem!r}")
        return 0
    try:
        getattr(command, verb)(args)
    except Refusal as refusal:
        print(f"deckhand: {refusal.rule}", file=sys.stderr)
        return 1
    return 0
