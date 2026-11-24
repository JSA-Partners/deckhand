from __future__ import annotations

from deckhand import config
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.store import Store
from deckhand.store.linear import LinearStore
from deckhand.store.local import LocalRecords, default_path


def open_store(role: str) -> Store:
    return LinearStore(connect(Credentials.load(config.config_dir()), role), LocalRecords(default_path()))
