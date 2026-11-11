"""A feature split into stories: one draft each, numbered, boarded and wired in the file's order."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from deckhand import draft, fleet, issue, log, park
from deckhand.step import Refusal, ref_label, resolved_settings


def split(repo: str, text: str, parked: int | None, named: str | None = None) -> int:
    """Open one draft per story of a feature, number them all, then record what waits on what.

    A story that belongs to another repository is parked there instead, with the requirements and
    its own sentence, for that repository's session to write. A feature these stories came from is
    the first of them rather than a second issue that closes: its number, its board item and
    everything already waiting on it stay where they are. Every write prints as it lands and
    nothing is retried, so a failure part way through leaves the printed lines as the record.
    """
    try:
        requirements, entries = draft.parse_split(text)
    except ValueError as error:
        raise Refusal(f"split file {error}") from error
    parked_url = park.unsplit(repo, parked) if parked is not None else None
    if parked is not None and entries[0].repo:
        raise Refusal(f"the first story of #{parked} is written into it, so it stays in this repository")
    settings = resolved_settings()
    stories = fleet.load(settings, archived=True)
    # Every draft carries the whole feature, so the first pass writes it unnumbered: no story can
    # name its siblings' numbers until every issue exists.
    provisional = draft.render(requirements, entries)
    origin = f"{repo}#{parked}" if parked is not None else f"a split in {repo}"
    parent = (repo, parked) if parked is not None else None
    feature = park.chosen(settings, named, park.feature_of(stories, parent))
    resolved = park.fields_for(settings, feature)
    numbers: list[int] = []
    urls: list[str] = []
    local: list[int] = []
    for position, entry in enumerate(entries):
        if entry.repo:
            body = draft.render(f"{requirements.rstrip()}\n\n{entry.sentence}", [])
            number, url = park.open_parked(
                settings, repo, entry.repo, entry.title, body, origin, stories, parent, feature, resolved
            )
        elif position == 0 and parked is not None:
            number, url = parked, parked_url or ""
            issue.set_title(repo, number, entry.title)
            print(f"Kept #{number} {entry.title}", flush=True)
            local.append(number)
        else:
            number, url = issue.create(repo, entry.title, provisional)
            print(f"Created #{number} {entry.title}", flush=True)
            local.append(number)
        numbers.append(number)
        urls.append(url)
    body = draft.render(requirements, [replace(e, number=n) for e, n in zip(entries, numbers, strict=True)])
    for number, url in zip(numbers, urls, strict=True):
        if number not in local:
            continue
        issue.update_body(repo, number, body)
        print(f"Numbered #{number}", flush=True)
        # A draft is not written yet, so its author logs that; it joins the feature it was split from now.
        park.board_draft(settings, repo, number, url, None, feature=feature, resolved=resolved)
    park.wire(repo, entries, numbers)
    siblings = [(e.repo or repo, n) for e, n in zip(entries[1:], numbers[1:], strict=True)]
    if parked is not None and siblings:  # one story shed nothing, so there is nothing to say or inherit
        named = ", ".join(ref_label(where, number, repo) for where, number in siblings)
        issue.comment(repo, parked, log.checked(f"Split: into {named}"))
        print("Logged Split", flush=True)
        park.inherit(repo, parked, siblings)
    # An agent gets no plugin-root substitution, so the launcher it is to run travels in the line.
    drafted = " ".join(f"#{number}" for number in local)
    print(f"Dispatch deckhand:author for each of {drafted} with {Path(sys.argv[0]).resolve()}")
    return 0
