from __future__ import annotations

from typing import Protocol

from deckhand.records import Record
from deckhand.ref import Ref


class Store(Protocol):
    """Every id is a Linear UUID."""

    def create_issue(
        self,
        id: str,
        team: str,
        title: str,
        description: str,
        project: str | None,
        labels: list[str],
    ) -> str: ...

    def issue(self, id: str) -> dict | None: ...

    def issue_by_ref(self, ref: Ref) -> dict | None: ...

    def set_labels(self, issue: str, labels: list[str]) -> None: ...

    def put_document(self, id: str, parent: str, title: str, body: str) -> None: ...

    def document(self, id: str) -> dict | None: ...

    def put_record(self, issue: str, record: Record) -> None: ...

    def records(self, issue: str) -> list[Record]: ...

    def relate(self, blocker: str, blocked: str) -> None: ...

    def relations(self, issue: str) -> list[tuple[str, str]]: ...
