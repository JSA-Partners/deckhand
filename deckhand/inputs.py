from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

from deckhand import answers, config, git, launch
from deckhand.step import Refusal, hand_role

CONFINED = "a hand reads files only from its worktree or the temporary directory, never the config or state dir"


def read(path: str, flag: str, usage: str) -> str:
    if not path:
        raise Refusal(f"{flag} is required; {usage}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        raise Refusal(f"{path}: cannot be read") from None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refusal(f"{path}: not a regular file")
        if hand_role() and not _allowed(_opened(fd)):
            raise Refusal(f"{path}: {CONFINED}")
        data = handle.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal(f"{path}: not valid UTF-8") from None


def read_or_none(path: Path) -> str | None:
    try:
        return read(str(path), "", "")
    except Refusal:
        return None


# The opened file's own path, so a symlink swapped after the check cannot redirect the read.
def _opened(fd: int) -> Path:
    if hasattr(fcntl, "F_GETPATH"):
        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        return Path(os.fsdecode(raw.split(b"\0", 1)[0])).resolve()
    return Path(os.readlink(f"/proc/self/fd/{fd}")).resolve()


def _allowed(path: Path) -> bool:
    denied = (config.config_dir(), answers.state_dir())
    if any(path.is_relative_to(Path(root).resolve()) for root in denied):
        return False
    roots = [path.resolve() for path in launch.input_roots()]
    try:
        roots.append(git.working_directory().resolve())
    except Refusal:
        pass
    return any(path.is_relative_to(root) for root in roots)
