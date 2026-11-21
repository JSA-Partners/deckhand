from __future__ import annotations

from datetime import datetime


def elapsed(at: str, now: float) -> float:
    return now - datetime.fromisoformat(at).timestamp()


def age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {rest}m" if rest else f"{hours}h"
    return f"{hours // 24}d"
