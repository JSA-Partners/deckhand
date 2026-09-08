"""Argument parsing and dispatch. Each subcommand maps to one function in one module."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence

from deckhand import __version__

Handler = Callable[[argparse.Namespace], int]
Configure = Callable[[argparse.ArgumentParser], None]
_REGISTRY: dict[str, tuple[Configure, Handler]] = {}


def command(name: str, configure: Configure) -> Callable[[Handler], Handler]:
    """Register `handler` as the `deckhand <name>` subcommand; `configure` adds its arguments."""

    def register(handler: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"command registered twice: {name}")
        _REGISTRY[name] = (configure, handler)
        return handler

    return register


def _summary(handler: Handler) -> tuple[str, str]:
    """Return `handler`'s docstring as `(full description, first-line help)`."""
    doc = (handler.__doc__ or "").strip()
    help_text = doc.splitlines()[0] if doc else ""
    return doc, help_text


def build_parser() -> argparse.ArgumentParser:
    import deckhand.commands  # noqa: F401  (side effect: every module imported so its decorators run)

    description = "A story process for shipping code."
    parser = argparse.ArgumentParser(prog="deckhand", description=description)
    parser.add_argument("--version", action="version", version=f"deckhand {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (configure, handler) in sorted(_REGISTRY.items()):
        sub_description, help_text = _summary(handler)
        sub = subparsers.add_parser(name, description=sub_description, help=help_text)
        configure(sub)
        sub.set_defaults(handler=handler)
    return parser


def main(argv: Sequence[str]) -> int:
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors=errors)
    try:
        parser = build_parser()
    except Exception as error:  # one line for the model, never a traceback
        print(f"deckhand: {error}", file=sys.stderr)
        return 1
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except Exception as error:  # one line for the model, never a traceback
        print(f"deckhand {args.command}: {error}", file=sys.stderr)
        return 1
