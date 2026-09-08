"""Project field reads and writes."""

from __future__ import annotations

import re
from collections.abc import Iterable

from deckhand import gh
from deckhand.config import Settings

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_NUMBER_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")


class NotOnBoard(gh.GhError):
    """The issue exists but has no item on the configured project board."""


_FIELD_VALUES_QUERY = (
    "query($id:ID!){node(id:$id){... on ProjectV2Item{fieldValues(first:50){nodes{"
    "... on ProjectV2ItemFieldNumberValue{number field{... on ProjectV2FieldCommon{name}}}"
    "... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}"
    "... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{name}}}"
    "... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{name}}}}}}}}"
)


def format_number(value: int | float) -> str:
    """Whole numbers print without a trailing `.0`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _value(node: dict) -> str | None:
    """One field value node as text, whatever type the project field has."""
    number_value = node.get("number")
    if number_value is not None:
        return format_number(number_value)
    for key in ("name", "date", "text"):
        text_value = node.get(key)
        if text_value is not None:
            return str(text_value)
    return None


def _values(settings: Settings, repo: str, number: int) -> dict[str, str]:
    """Every set field on the issue's board item by name; empty when the issue is off the board."""
    settings.require_project()
    item = gh.item_id(settings, repo, number)
    if item is None:
        return {}
    data = gh.graphql(_FIELD_VALUES_QUERY, {"id": item})[0]
    values: dict[str, str] = {}
    for node in data["data"]["node"]["fieldValues"]["nodes"]:
        name = (node.get("field") or {}).get("name")
        value = _value(node)
        if name is not None and value is not None:
            values.setdefault(name, value)  # the first node wins, as a single read of one field does
    return values


def get_fields(settings: Settings, repo: str, number: int, names: Iterable[str]) -> dict[str, str | None]:
    """Each name's value on the issue's board item, in one pass; None where it is unset or off board.

    A step that needs several fields asks once: the board answers every field of an item in the
    same query, so reading them one at a time is one round trip per name for nothing.
    """
    values = _values(settings, repo, number)
    return {name: values.get(name) for name in names}


def get_field(settings: Settings, repo: str, number: int, field: str) -> str | None:
    """The item's value for `field`, or None when it is unset or the issue is off the board."""
    return get_fields(settings, repo, number, (field,))[field]


def set_field(settings: Settings, repo: str, number: int, field: str, value: str) -> str:
    """Set one project field on the issue's board item; returns `FIELD=VALUE`."""
    settings.resolve()  # the owner is needed either way, and an unreadable link must not read as off-board
    item = gh.item_id(settings, repo, number)
    if item is None:
        raise NotOnBoard(
            f"issue #{number} is not on project {settings.owner}/{settings.project}; run 'gh project item-add' first"
        )
    field_json = gh.field(settings, field)
    if field_json["type"] == "ProjectV2SingleSelectField":
        option = next((o["id"] for o in field_json["options"] if o["name"] == value), None)
        if option is None:
            names = ", ".join(o["name"] for o in field_json["options"])
            raise gh.GhError(f"no option '{value}' on field '{field}' (options: {names})")
        flag = ["--single-select-option-id", option]
    elif _DATE_RE.match(value):
        flag = ["--date", value]
    elif _NUMBER_RE.match(value):
        flag = ["--number", value]
    else:
        flag = ["--text", value]
    args = [
        "project",
        "item-edit",
        "--id",
        item,
        "--project-id",
        gh.project_id(settings),
        "--field-id",
        field_json["id"],
        *flag,
    ]
    gh.run(*args)
    return f"{field}={value}"
