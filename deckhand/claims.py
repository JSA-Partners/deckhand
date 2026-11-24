from __future__ import annotations

from deckhand.step import Refusal
from deckhand.store import Store


def me(person: Store) -> str:
    return person.viewer()["id"]


def owned(story: dict, me: str) -> None:
    if story["assignee"] not in (None, me):
        raise Refusal(f"{story['identifier']} belongs to {story['assignee_name']}")


def claim(person: Store, story: dict, me: str) -> None:
    owned(story, me)
    if story["assignee"] != me:
        person.assign(story["id"], me)
