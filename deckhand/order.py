from __future__ import annotations

import heapq
from collections import defaultdict

from deckhand.features import Feature
from deckhand.store import Store

DONE = "Done"


def edges(store: Store, feature: Feature) -> list[tuple[str, str]]:
    live = {s["id"] for s in feature.live}
    pairs = {pair for s in feature.live for pair in store.relations(s["id"])}
    return sorted(pair for pair in pairs if pair[0] in live and pair[1] in live)


def build_order(stories: list[dict], edges: list[tuple[str, str]]) -> tuple[list[dict], list[str]]:
    by_id = {s["id"]: s for s in stories}
    after: dict[str, set[str]] = defaultdict(set)
    waiting = dict.fromkeys(by_id, 0)
    for blocker, blocked in edges:
        if blocker in by_id and blocked in by_id and blocked not in after[blocker]:
            after[blocker].add(blocked)
            waiting[blocked] += 1
    ready_now = [(by_id[i]["number"], i) for i in by_id if waiting[i] == 0]
    heapq.heapify(ready_now)
    order: list[dict] = []
    while ready_now:
        _, id = heapq.heappop(ready_now)
        order.append(by_id[id])
        for nxt in after[id]:
            waiting[nxt] -= 1
            if waiting[nxt] == 0:
                heapq.heappush(ready_now, (by_id[nxt]["number"], nxt))
    placed = {s["id"] for s in order}
    rest = sorted((s for s in stories if s["id"] not in placed), key=lambda s: s["number"])
    cycle = [s["identifier"] for s in rest if _reaches_itself(s["id"], after)]
    return order + rest, cycle


def unblocks(story_id: str, edges: list[tuple[str, str]], open_ids: set[str]) -> int:
    after: dict[str, set[str]] = defaultdict(set)
    for blocker, blocked in edges:
        after[blocker].add(blocked)
    seen: set[str] = set()
    stack = [story_id]
    while stack:
        for nxt in after[stack.pop()]:
            if nxt in open_ids and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    seen.discard(story_id)
    return len(seen)


def ready(story: dict, edges: list[tuple[str, str]], by_id: dict[str, dict]) -> bool:
    blockers = [blocker for blocker, blocked in edges if blocked == story["id"]]
    return all(by_id.get(blocker, {}).get("state") == DONE for blocker in blockers)


def _reaches_itself(id: str, after: dict[str, set[str]]) -> bool:
    seen: set[str] = set()
    stack = list(after[id])
    while stack:
        nxt = stack.pop()
        if nxt == id:
            return True
        if nxt not in seen:
            seen.add(nxt)
            stack.extend(after[nxt])
    return False
