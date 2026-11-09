"""How long deckhand waits on a command, by the wall clock, and what it says when it gives up.

A command left on a connection the Mac slept through never answers, and a monotonic timeout stops
counting while the Mac sleeps, so the deadline is read from the wall clock.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time

LIMIT = 120.0
_now = time.time
_WRITES = {
    "issue": {"create", "edit", "close", "comment"},
    "pr": {"create", "edit", "close", "comment", "merge"},
    "project": {"item-edit", "item-add", "item-archive", "item-delete", "field-create", "field-delete", "link"},
    "label": {"create"},
    "repo": {"edit"},
}


class Expired(Exception):
    """The command ran past the deadline and was killed."""


def writes(args: tuple[str, ...], stdin: str | None = None) -> bool:
    """Whether a gh call changes GitHub, so a lost answer may hide a write that landed.

    A GraphQL call sends its query on stdin, so a mutation is read from there as well as from a
    `query=` field.
    """
    if len(args) > 1 and args[1] in _WRITES.get(args[0], set()):
        return True
    if args[:1] != ("api",):
        return False
    method = next((args[i + 1] for i, arg in enumerate(args[:-1]) if arg in ("-X", "--method")), "GET")
    query = next((arg.partition("=")[2] for arg in args if arg.startswith("query=")), "")
    return method.upper() != "GET" or query.lstrip().startswith("mutation") or '"mutation' in (stdin or "")


def message(tool: str, args: tuple[str, ...], stdin: str | None = None) -> str:
    """What a call that ran out of time says, warning when it may have written."""
    landed = "; it may have landed; check before running it again" if writes(args, stdin) else ""
    return f"{tool} {' '.join(args[:2])} gave no answer in {LIMIT:g} seconds{landed}"


def wait(process: subprocess.Popen[str], stdin: str | None) -> tuple[str, str]:
    """Stdout and stderr of `process`; past the deadline its whole process group is killed.

    The group goes, not the process alone, because a child left holding the pipe open would keep
    the read waiting after the parent died.
    """
    give_up = _now() + LIMIT
    sent = stdin
    try:
        while True:
            try:
                return process.communicate(input=sent, timeout=1)
            except subprocess.TimeoutExpired:
                sent = None
                if _now() >= give_up:
                    raise Expired from None
    except BaseException:
        # Its own session keeps a Ctrl-C from reaching it, so whatever ends the wait ends the call.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
