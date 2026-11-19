from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from deckhand import ops
from deckhand.records import content_hash
from deckhand.store import Store

if TYPE_CHECKING:
    from deckhand.features import Feature

TITLE = "Spike: "
FOLDER = "spikes"


def key(project_id: str, question: str) -> str:
    return content_hash(f"{project_id}\0{question}")[:12]


def answer_id(project_id: str, key: str) -> str:
    return ops.create_id(project_id, "spike:" + key)


def key_of(project_id: str, document: dict) -> str | None:
    title = document["title"]
    if not title.startswith(TITLE):
        return None
    found = key(project_id, title.removeprefix(TITLE))
    return found if answer_id(project_id, found) == document["id"] else None


def answered(store: Store, feature: Feature) -> list[dict]:
    project = feature.project["id"]
    return [d for d in store.project_documents(project) if key_of(project, d) is not None]


def question_path(state: Path, key: str) -> Path:
    return state / FOLDER / f"{key}.md"


def keys(state: Path, project_id: str) -> set[str]:
    found = set()
    for path in sorted((state / FOLDER).glob("*.md")):
        try:
            question = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if key(project_id, question) == path.stem:
            found.add(path.stem)
    return found
