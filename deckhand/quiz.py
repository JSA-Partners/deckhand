from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from deckhand import review, versions
from deckhand.questions import MAX_OPTIONS, MIN_OPTIONS, RECOMMENDED
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.tiers import Tier

SET = "quiz-set"
KIND = "quiz"
BASE = {0: 0, 1: 1, 2: 1, 3: 3}
AROUND = 5
FIELDS = ("question", "options", "correct", "where")

Reader = Callable[[Path], str]


@dataclass(frozen=True)
class Item:
    question: str
    options: list[str]
    correct: int
    where: str


def quiz_size(tier: Tier, new_here: int) -> int:
    return BASE[tier.level] + new_here


def location(where: str) -> tuple[str, int]:
    path, _, line = where.rpartition(":")
    if not path or not line.isdigit() or int(line) < 1:
        raise Refusal(f"where must be path:line with a line from 1, not {where!r}")
    if path.startswith("/") or ".." in path.split("/"):
        raise Refusal(f"where must name a relative path without .., not {path!r}")
    return path.removeprefix("./"), int(line)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"{where} must be a non-empty string")
    return value.strip()


def _options(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not MIN_OPTIONS <= len(value) <= MAX_OPTIONS:
        raise Refusal(f"{where} must be a list of {MIN_OPTIONS} to {MAX_OPTIONS} options")
    options = [_text(option, f"{where} entry") for option in value]
    if len(set(options)) != len(options):
        raise Refusal(f"{where} holds a duplicate option")
    if any(RECOMMENDED.search(option) for option in options):
        raise Refusal(f"{where}: an option must not call itself recommended")
    return options


def _item(entry: object, n: int) -> Item:
    if not isinstance(entry, dict):
        raise Refusal(f"quiz item {n} must be an object")
    for name in entry:
        if name not in FIELDS:
            raise Refusal(f"quiz item {n} has an unknown field {name}")
    options = _options(entry.get("options"), f"quiz item {n} options")
    correct = entry.get("correct")
    if not isinstance(correct, int) or isinstance(correct, bool) or not 0 <= correct < len(options):
        raise Refusal(f"quiz item {n} correct must be an index into its {len(options)} options")
    where = _text(entry.get("where"), f"quiz item {n} where")
    path, line = location(where)
    return Item(_text(entry.get("question"), f"quiz item {n} question"), options, correct, f"{path}:{line}")


def parse_quiz(text: str, size: int) -> list[Item]:
    try:
        document = json.loads(text)
    except ValueError:
        raise Refusal("the quiz is not valid JSON") from None
    if not isinstance(document, list):
        raise Refusal("the quiz must be a JSON list of items")
    if len(document) != size:
        raise Refusal(f"the quiz has {len(document)} items; its size by tier and new_here is {size}")
    return [_item(entry, n) for n, entry in enumerate(document, start=1)]


def set_version(head: str, items: list[Item]) -> str:
    asked = [{"question": i.question, "options": sorted(i.options), "where": i.where} for i in items]
    key = content_hash(json.dumps([i.options[i.correct] for i in items]))
    return content_hash(json.dumps({"head": head, "items": asked, "key": key}, sort_keys=True))


def shown_options(version: str, n: int, options: list[str]) -> list[str]:
    return random.Random(f"{version}:{n}").sample(sorted(options), len(options))


def set_record(story: str, head: str, items: list[Item], summary_digest: str, new_here: int) -> Record:
    data = {"head": head, "items": [asdict(i) for i in items], "summary": summary_digest, "new_here": new_here}
    return Record(SET, story, set_version(head, items), "agent:quiz", data)


def held_set(records: list[Record], story: str) -> Record | None:
    return versions.held(records, SET, story)


def items_of(record: Record) -> list[Item]:
    return [Item(**entry) for entry in record.data.get("items", [])]


def subject(story: str, n: int) -> str:
    return f"{story}:{n}"


def _graded(records: list[Record], story: str, n: int) -> bool | None:
    found = held_set(records, story)
    if found is None or not 1 <= n <= len(found.data.get("items", [])):
        return None
    answer = versions.held(records, KIND, subject(story, n))
    if answer is None or answer.version != found.version or not answer.author.startswith(review.PERSON):
        return None
    return answer.data.get("correct") is True


def right(records: list[Record], story: str, n: int) -> bool:
    return _graded(records, story, n) is True


def missed(records: list[Record], story: str, n: int) -> bool:
    return _graded(records, story, n) is False


def open_items(records: list[Record], story: str) -> list[int]:
    found = held_set(records, story)
    count = len(found.data.get("items", [])) if found else 0
    return [n for n in range(1, count + 1) if not right(records, story, n)]


def passed(records: list[Record], story: str, head: str) -> bool:
    found = held_set(records, story)
    return found is not None and found.data.get("head") == head and not open_items(records, story)


def grade(records: list[Record], asked: str, version: str, options: list[str], answer: str) -> bool:
    story, _, n = asked.rpartition(":")
    found = held_set(records, story)
    items = items_of(found) if found is not None else []
    if found is None or found.version != version or not n.isdigit() or not 1 <= int(n) <= len(items):
        raise Refusal("the quiz changed since the question was asked; ask the item again")
    item = items[int(n) - 1]
    if sorted(item.options) != sorted(options):
        raise Refusal("the quiz changed since the question was asked; ask the item again")
    return answer == item.options[item.correct]


def code_at(worktree: Path, where: str, reader: Reader) -> list[str]:
    path, line = location(where)
    try:
        lines = reader(worktree / path).splitlines()
    except (OSError, UnicodeDecodeError):
        return [f"{path} cannot be read in the worktree"]
    if line > len(lines):
        return [f"{path} has {len(lines)} lines, fewer than {line}"]
    first, last = max(1, line - AROUND), min(len(lines), line + AROUND)
    return [f"{'>' if n == line else ' '} {n} {lines[n - 1]}" for n in range(first, last + 1)]
