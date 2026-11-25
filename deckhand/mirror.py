from __future__ import annotations

import json
from collections.abc import Mapping

from deckhand import chores, features, ops, planning, signoff, versions
from deckhand.actions import Attach, Mirror, PutDocument
from deckhand.records import Record, content_hash
from deckhand.store import Store

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


def _approvals(store: Store, feature: features.Feature) -> list[Attach]:
    held = [(story, found) for story in feature.live if (found := _approval(feature, story)) is not None]
    contract = planning.document(store, feature, "Contract") if held else None
    if contract is None:
        return []
    found = []
    for story, record in held:
        name = str(record.data["name"])
        metadata = {"version": record.version, "approvedBy": name, "approvedAt": record.at}
        found.append(_attach(story["id"], contract["url"], f"Plan approved by {name}", "{approvedAt__since}", metadata))
    return found


def _sign_off(records: list[Record], story: str) -> Attach | None:
    held = signoff.signed(records, story)
    if held is None or held.data.get("answer") != signoff.SIGN:
        return None
    name, repo = held.data.get("name"), held.data.get("repo")
    if not name or not repo:
        return None
    sha = signoff.signed_head(held)
    url = COMMIT_URL.format(repo=repo, sha=sha)
    metadata = {"sha": sha, "signedBy": str(name), "signedAt": held.at}
    void = signoff.voided(records, story, held)
    if void is None:
        return _attach(story, url, f"Signed off by {name}", "{signedAt__since}", metadata)
    reason = str(void.data.get("reason", ""))
    return _attach(story, url, f"Sign-off by {name} voided", reason, metadata | {"void": void.at})


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


def wanted(store: Store, feature: features.Feature) -> list[Attach | PutDocument]:
    found: list[Attach | PutDocument] = list(_approvals(store, feature))
    for story in feature.live:
        held = _sign_off(feature.records.get(story["id"], []), story["id"])
        found += [held] if held is not None else []
    document = _story_map(feature)
    return found + ([document] if document is not None else [])


def due(store: Store, feature: features.Feature, mirrored: Mapping[str, str]) -> list[Mirror]:
    return [action for action in wanted(store, feature) if mirrored.get(action.key) != action.digest]
