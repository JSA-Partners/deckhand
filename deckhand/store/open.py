from __future__ import annotations

from deckhand import budget, config
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.linear.meter import Meter
from deckhand.step import Refusal
from deckhand.store import Store
from deckhand.store.cached import CachedStore
from deckhand.store.linear import LinearStore
from deckhand.store.local import LocalRecords, default_path
from deckhand.store.state import LocalState

METERS = {"app": Meter(), "person": Meter()}


def open_store(role: str, teams: dict[str, str] | None = None) -> Store:
    known = teams if teams is not None else {}
    for key, id in config.local().teams.items():
        known.setdefault(key, id)
    client = connect(Credentials.load(config.config_dir()), role, meter=METERS[role])
    return CachedStore(LinearStore(client, LocalRecords(default_path()), known))


# A meter that cannot be kept never changes what the command printed or how it exited.
def flush() -> None:
    taken = {role: meter.taken() for role, meter in METERS.items()}
    if not any(taken.values()):
        return
    try:
        with LocalState(default_path()) as rows:
            for role, found in taken.items():
                if found is not None:
                    budget.keep(rows, role, found)
    except Refusal:
        pass


class Stores:
    def __init__(
        self, app: Store | None = None, person: Store | None = None, teams: dict[str, str] | None = None
    ) -> None:
        self._held: dict[str, Store | None] = {"app": app, "person": person}
        self._teams = teams

    def __call__(self, role: str) -> Store:
        held = self._held[role]
        if held is None:
            held = self._held[role] = open_store(role, self._teams)
        return held
