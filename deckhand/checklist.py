"""The setup checklist: what a project and its repository must have, read back and never guessed.

Each item names what is left and where a person clicks to do it. `setup context` reports every
item; `setup apply` prints the ones the API cannot do and lists apart the ones it could not read.
Nothing here writes; the one write the checklist informs, recoloring Kind, is `setup apply`'s.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from deckhand import board, gh
from deckhand.config import Settings
from deckhand.step import reason

STATUS_OPTIONS = ["Draft", "Backlog", "In Progress", "Pending Review", "Done"]
BOARD_FIELDS = ["Title", "Status", "Kind", "Story Points", "Actual", "Assignees", "Repository"]
KIND_COLORS = {"feat": "GREEN", "fix": "RED", "chore": "GRAY", "refactor": "BLUE", "docs": "PURPLE", "perf": "ORANGE"}
OTHER_COLOR = "YELLOW"

# The repository settings `setup apply` writes, as the REST repository object reports them.
MERGE_SETTINGS: dict[str, Any] = {
    "allow_squash_merge": True,
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
    "delete_branch_on_merge": True,
    "squash_merge_commit_message": "PR_BODY",
}

UNKNOWN = "unknown ("

# What a person should know before the click, by item; only a Status option's deletion is permanent.
NOTES = {"Status options": "Deleting an option is permanent."}


class Item(NamedTuple):
    """One checklist item: its name, the click that finishes it, and what is left, None when done."""

    name: str
    click: str
    left: str | None

    @property
    def unknown(self) -> bool:
        """Whether the item could not be read, as opposed to being done or left to do."""
        return self.left is not None and self.left.startswith(UNKNOWN)


def kind_options(kinds: list[str]) -> list[dict[str, str]]:
    """The Kind options as the API takes them, each with its color; a kind not in the table is yellow."""
    return [{"name": kind, "color": KIND_COLORS.get(kind, OTHER_COLOR), "description": ""} for kind in kinds]


def kind_update(kind: dict[str, Any], kinds: list[str]) -> list[dict[str, str]] | None:
    """The options to write so Kind carries every configured kind in its color, or None when it does.

    The update replaces the whole option list, so every option goes in: the configured kinds in
    their order, each by its existing id so no story loses its value, then whatever a person added,
    kept as it is.
    """
    existing = {o["name"]: o for o in kind.get("options") or []}
    wanted = kind_options(kinds)
    if all(o["name"] in existing and existing[o["name"]].get("color") == o["color"] for o in wanted):
        return None
    options: list[dict[str, str]] = []
    for option in wanted:
        found = existing.get(option["name"])
        options.append({"id": found["id"], **option} if found else option)
    for name, found in existing.items():
        if name not in kinds:
            options.append(
                {"id": found["id"], "name": name, "color": found.get("color") or OTHER_COLOR, "description": ""}
            )
    return options


def _status_item(fields: list[dict[str, Any]] | None, unread: str) -> Item:
    click = "Project > Settings > Status"
    if fields is None:
        return Item("Status options", click, f"{UNKNOWN}{unread})")
    status = next((f for f in fields if f["name"] == "Status"), None)
    current = [o["name"] for o in (status.get("options") or [])] if status else []
    if current == STATUS_OPTIONS:
        return Item("Status options", click, None)
    return Item(
        "Status options", click, f"set to {', '.join(STATUS_OPTIONS)} (currently: {', '.join(current) or 'none'})"
    )


def _board_item(settings: Settings) -> Item:
    click = "Board view > view menu > Fields"
    try:
        shown = board.view_fields(settings).get("Board")
    except Exception as error:
        return Item("Board view fields", click, f"{UNKNOWN}{reason(error)})")
    if shown is None:
        return Item("Board view fields", click, "no view named Board")
    # The view menu only toggles a field on or off, so the order it reports is GitHub's, never a person's.
    if set(shown) == set(BOARD_FIELDS):
        return Item("Board view fields", click, None)
    missing = [name for name in BOARD_FIELDS if name not in shown]
    extra = [name for name in shown if name not in BOARD_FIELDS]
    parts = [f"turn on {', '.join(missing)}"] if missing else []
    parts += [f"turn off {', '.join(extra)}"] if extra else []
    return Item("Board view fields", click, f"on the Board view {' and '.join(parts)}")


def _kind_item(fields: list[dict[str, Any]] | None, kinds: list[str], unread: str) -> Item:
    click = "Project > Settings > Kind"
    if fields is None:
        return Item("Kind colors", click, f"{UNKNOWN}{unread})")
    kind = next((f for f in fields if f["name"] == "Kind"), None)
    if kind is None:
        return Item("Kind colors", click, "Kind is missing; apply creates it")
    if kind.get("dataType") and kind["dataType"] != "SINGLE_SELECT":
        return Item("Kind colors", click, f"Kind is {kind['dataType']}, not a single select")
    colors = {o["name"]: o.get("color") for o in kind.get("options") or []}
    off = []
    for option in kind_options(kinds):
        name, color = option["name"], option["color"]
        if name not in colors:
            off.append(f"add {name} {color.lower()}")
        elif colors[name] != color:
            off.append(f"set {name} {color.lower()}")
    return Item("Kind colors", click, ", ".join(off) or None)


def _merge_item(repo: str | None) -> Item:
    click = "Repository > Settings > General > Pull Requests"
    if not repo:
        return Item("Merge settings", click, f"{UNKNOWN}no repository)")
    try:
        actual = gh.merge_settings(repo)
    except Exception as error:
        return Item("Merge settings", click, f"{UNKNOWN}{reason(error)})")
    off = [key for key, value in MERGE_SETTINGS.items() if actual.get(key) != value]
    if not off:
        return Item("Merge settings", click, None)
    current = ", ".join(f"{key} {str(actual.get(key)).lower()}" for key in off)
    return Item(
        "Merge settings",
        click,
        "make squash the only merge, with the pull request body as its message, and delete the branch "
        f"on merge (currently: {current})",
    )


def checklist(
    settings: Settings, repo: str | None, fields: list[dict[str, Any]] | None, unread: str | None = None
) -> list[Item]:
    """Every item, in the order the person meets them; `fields` is the project's, None when unread.

    `unread` is why the fields could not be read, so the items built from them can say so; the
    view and the merge settings are read here and say so themselves.
    """
    why = unread or "the project fields were not read"
    return [
        _status_item(fields, why),
        _board_item(settings),
        _kind_item(fields, settings.kinds, why),
        _merge_item(repo),
    ]
