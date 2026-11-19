from __future__ import annotations

from deckhand import config
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.step import Refusal
from deckhand.store import Store
from deckhand.store.linear import LinearStore


def person_store() -> Store:
    return LinearStore(connect(Credentials.load(config.config_dir()), "person"))


def me(person: Store) -> str:
    return person.viewer()["id"]


def owned(story: dict, me: str) -> None:
    if story["assignee"] not in (None, me):
        raise Refusal(f"{story['identifier']} belongs to {story['assignee_name']}")


def claim(person: Store, story: dict, me: str) -> None:
    owned(story, me)
    if story["assignee"] != me:
        person.assign(story["id"], me)
