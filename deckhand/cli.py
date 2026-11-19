from __future__ import annotations

import importlib
import pkgutil
import sys

from deckhand import steps
from deckhand.git import GitError
from deckhand.linear.client import LinearError
from deckhand.step import REGISTRY, VERBS, Refusal, hand_may_apply, hand_role


def _load_steps() -> None:
    for module in pkgutil.iter_modules(steps.__path__):
        try:
            importlib.import_module(f"{steps.__name__}.{module.name}")
        except Exception as problem:
            print(f"deckhand: cannot load step {module.name}: {problem}", file=sys.stderr)


def main(argv: list[str]) -> int:
    _load_steps()
    if hand_role() and argv[1:2] == ["apply"] and not hand_may_apply(argv[0], argv[2:]):
        print(f"deckhand: a hand cannot run {argv[0]} apply; leave it for the person's captain", file=sys.stderr)
        return 1
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
        except Refusal as refusal:
            print(f"deckhand: {argv[0]} context: {refusal.rule}")
        except GitError as error:
            print(f"deckhand: {argv[0]} context: {error.command} failed (exit {error.code}): {error.stderr_tail}")
        except Exception as problem:
            print(f"deckhand: {argv[0]} context could not be read: {type(problem).__name__}")
        return 0
    try:
        getattr(command, verb)(args)
    except Refusal as refusal:
        print(f"deckhand: {refusal.rule}", file=sys.stderr)
        return 1
    except LinearError as error:
        print(f"deckhand: {error.code}: {error.message}", file=sys.stderr)
        return 1
    except GitError as error:
        print(f"deckhand: {argv[0]} failed (exit {error.code}): {error.stderr_tail}", file=sys.stderr)
        return 1
    except Exception as problem:
        print(f"deckhand: {argv[0]} apply failed: {type(problem).__name__}", file=sys.stderr)
        return 1
    return 0
