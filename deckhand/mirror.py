from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace

from deckhand import chores, features, ops, signoff, versions
from deckhand.actions import Attach, Mirror, PutDocument
from deckhand.records import Record, content_hash

PREFIX = "mirror:"
STORY_MAP = "Story map"
COMMIT_URL = "https://github.com/{repo}/commit/{sha}"


def _attach(issue: str, url: str, title: str, subtitle: str, metadata: dict[str, str]) -> Attach:
    fields = (issue, url, title, subtitle, tuple(metadata.items()))
    digest = content_hash(json.dumps(fields))
    return Attach(f"{PREFIX}{issue}", f"{PREFIX}{issue}:{url}", digest, *fields)


def _approval(feature: features.Feature, story: dict) -> Record | None:
    held = versions.held(feature.records.get(story["id"], []), features.APPROVAL, feature.project["id"])
    return held if held is not None and held.data.get("name") else None


def _approvals(feature: features.Feature, contract: str) -> list[Attach]:
    found = []
    for story in feature.live if contract else []:
        record = _approval(feature, story)
        if record is not None:
            name = str(record.data["name"])
            metadata = {"version": record.version, "approvedBy": name, "approvedAt": record.at}
            title = f"Plan approved by {name}"
            found.append(_attach(story["id"], contract, title, "{approvedAt__since}", metadata))
    return found


def _commit(story: str, data: dict, sha: str, at: str) -> tuple[str, str, dict[str, str]] | None:
    name, repo = data.get("name"), data.get("repo")
    if not name or not repo:
        return None
    return str(name), COMMIT_URL.format(repo=repo, sha=sha), {"sha": sha, "signedBy": str(name), "signedAt": at}


# Every void is resent, so a re-sign landing before a void was written still marks the old head voided.
def _sign_offs(records: list[Record], story: str) -> list[Attach]:
    found: dict[str, Attach] = {}
    for void in sorted((r for r in records if r.kind == signoff.VOID), key=lambda r: r.at):
        shown = _commit(story, void.data, void.version, str(void.data.get("signed_at", "")))
        if shown is not None:
            name, url, metadata = shown
            reason = str(void.data.get("reason", ""))
            held = _attach(story, url, f"Sign-off by {name} voided", reason, metadata | {"void": void.at})
            found[held.key] = held
    sign = signoff.signed(records, story)
    if sign is not None and sign.data.get("answer") == signoff.SIGN and signoff.voided(records, story, sign) is None:
        shown = _commit(story, sign.data, signoff.signed_head(sign), sign.at)
        if shown is not None:
            name, url, metadata = shown
            held = _attach(story, url, f"Signed off by {name}", "{signedAt__since}", metadata)
            found[held.key] = held
    return list(found.values())


def _story_lines(feature: features.Feature, story: dict) -> list[str]:
    records = feature.records.get(story["id"], [])
    level = versions.tier(records, story["id"])
    lines = [f"## {story['identifier']} {story['title']}", "", f"Tier: {level.label if level else 'not given'}"]
    items = [
        f"- {item}: {said}"
        for item, draft in versions.drafts(records).items()
        if (said := versions.answer(records, item, draft)) is not None
    ]
    planned = chores.after_items(records, story["id"])
    for n, text in enumerate(planned or versions.after_merge(records), start=1):
        status = chores.after_status(records, story["id"], n, text)[0] if planned else chores.OPEN
        items.append(f"- After merge: {text} ({status})")
    return [*lines, *([""] + items if items else [])]


# A fresh state folder holds no map records, so it never overwrites the map with an empty one.
def _story_map(feature: features.Feature) -> PutDocument | None:
    live = feature.live
    if not live or not all(any(r.kind == "stub" for r in feature.records.get(s["id"], [])) for s in live):
        return None
    body = "\n\n".join("\n".join(_story_lines(feature, s)) for s in live) + "\n"
    project = feature.project["id"]
    fields = (ops.create_id(project, "story-map"), project, STORY_MAP, body)
    return PutDocument(f"{PREFIX}{project}", f"{PREFIX}{project}:story-map", content_hash(json.dumps(fields)), *fields)


def needs_contract(feature: features.Feature) -> bool:
    return any(_approval(feature, story) is not None for story in feature.live)


# The Contract URL comes from the last link, so an approval named since then waits for the next one.
def wanted(feature: features.Feature, contract: str) -> list[Mirror]:
    found: list[Mirror] = list(_approvals(feature, contract))
    for story in feature.live:
        found += _sign_offs(feature.records.get(story["id"], []), story["id"])
    document = _story_map(feature)
    return found + ([document] if document is not None else [])


def due(feature: features.Feature, contract: str, mirrored: Mapping[str, str]) -> list[Mirror]:
    return [action for action in wanted(feature, contract) if mirrored.get(action.key) != action.digest]


# The answer that settles a feature's last chore drops it from the read, so it stays held until that answer is mirrored.
def leaving(
    before: list[features.Feature],
    after: list[features.Feature],
    records: Callable[[str], list[Record]],
    mirrored: Mapping[str, str],
) -> list[features.Feature]:
    kept = {f.project["id"] for f in after}
    gone = [f for f in before if f.project["id"] not in kept]
    fresh = [replace(f, records={s["id"]: records(s["id"]) for s in f.stories}) for f in gone]
    return [f for f in fresh if due(f, "", mirrored)]
