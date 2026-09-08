"""Import every module in the package so their @command decorators register.

Discovery replaces a hand-maintained import list: adding a module is enough, and a module that fails
to import still surfaces through cli.main as a one-line error.
"""

from __future__ import annotations

import importlib
import pkgutil

import deckhand

for _module in pkgutil.iter_modules(deckhand.__path__):
    if _module.name not in {"cli", "commands"}:
        importlib.import_module(f"deckhand.{_module.name}")
