from __future__ import annotations

from datetime import datetime
from pathlib import Path

from deckhand import versions
from deckhand.features import Feature
from deckhand.store import Store
from deckhand.tiers import Tier

FAILURE = "If this fails halfway, what is left behind and what does the person see?"
RETRY = "If this runs twice or retries, what must stay true?"
DATA_RULE = "Which data rule does this enforce, and what exactly is refused?"
CONTRACT = "Which contract does this change, and who relies on it today?"
HOUR = 60
STARTED = "sitting"


def questions_for(
    issue: dict, tier: Tier, crosses: bool, unknowns: list[str], new_here: list[str], contract_names: list[str]
) -> list[str]:
    asked = []
    if tier.level == 3:
        asked += [FAILURE, RETRY]
    if tier.data_rule:
        asked.append(DATA_RULE)
    text = f"{issue['title']}\n{issue['description'] or ''}".lower()
    if crosses or any(name.lower() in text for name in contract_names if name.strip()):
        asked.append(CONTRACT)
    asked += [f"Unknown: {item}. What is known, and what would settle it?" for item in unknowns]
    asked += [f"New here: {item}. What should it do, in your words?" for item in new_here]
    return asked


def stories_left(store: Store, feature: Feature) -> list[dict]:
    left = []
    for story in feature.live:
        done = versions.held(feature.records.get(story["id"], []), "sitting", story["id"])
        if done is None or not versions.current(store, feature, done):
            left.append(story)
    return left


def progress(store: Store, feature: Feature, started_at: datetime, now: datetime) -> str:
    minutes = max(0, int((now - started_at).total_seconds() // 60))
    line = f"{minutes} minutes in the sitting, {len(stories_left(store, feature))} stories left"
    if minutes >= HOUR:
        line += "; the sitting has passed an hour, a break or a second sitting may serve better"
    return line


def started(state: Path, project: str, now: datetime) -> datetime:
    path = state / STARTED / project
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(now.isoformat(), encoding="utf-8")
    return now
