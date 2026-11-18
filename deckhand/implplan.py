from __future__ import annotations

import hashlib
import json

from deckhand.step import Refusal

CHANGES = ("create", "modify")
SHAPE = {
    "files": 'a list of {"path", "change"}, change is create or modify',
    "criteria": 'a list of {"text", "tests"}, tests a non-empty list of test names',
    "docs_false": 'a list of {"path", "sentence"}, the docs this story makes false',
    "contract_sites": "a list of paths where the Contract is honored",
    "invariant_sites": "a list of paths where an Invariant is enforced",
    "after_merge": "optional, a list of follow-ups to do after the merge",
}
_REQUIRED = ("files", "criteria", "docs_false", "contract_sites", "invariant_sites")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _path(value: object, where: str) -> str:
    if not _text(value):
        raise Refusal(f"{where} must be a non-empty string")
    path = str(value).strip().removeprefix("./")
    if path.startswith("/") or ".." in path.split("/"):
        raise Refusal(f"{where} must be a relative path without .., not {path!r}")
    return path


def _entries(manifest: dict, name: str, fields: tuple[str, ...]) -> list[dict]:
    value = manifest[name]
    if not isinstance(value, list):
        raise Refusal(f"manifest field {name} must be a list")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise Refusal(f"{name} entry {index} must be an object")
        for field in entry:
            if field not in fields:
                raise Refusal(f"{name} entry {index} has an unknown field {field}")
    return value


def _strings(manifest: dict, name: str) -> None:
    value = manifest[name]
    if not isinstance(value, list):
        raise Refusal(f"manifest field {name} must be a list")
    for index, item in enumerate(value):
        if not _text(item):
            raise Refusal(f"{name} entry {index} must be a non-empty string")


def parse_manifest(text: str) -> dict:
    try:
        manifest = json.loads(text)
    except ValueError:
        raise Refusal("the manifest is not valid JSON") from None
    if not isinstance(manifest, dict):
        raise Refusal("the manifest must be a JSON object")
    for name in manifest:
        if name not in SHAPE:
            raise Refusal(f"the manifest has an unknown field {name}")
    for name in _REQUIRED:
        if name not in manifest:
            raise Refusal(f"manifest field {name} is missing")
    seen: set[str] = set()
    for index, entry in enumerate(_entries(manifest, "files", ("path", "change"))):
        entry["path"] = _path(entry.get("path"), f"files entry {index} field path")
        if entry["path"] in seen:
            raise Refusal(f"files entry {index} field path is a duplicate of {entry['path']}")
        seen.add(entry["path"])
        if entry.get("change") not in CHANGES:
            raise Refusal(f"files entry {index} field change must be create or modify")
    for index, entry in enumerate(_entries(manifest, "criteria", ("text", "tests"))):
        if not _text(entry.get("text")):
            raise Refusal(f"criteria entry {index} field text must be a non-empty string")
        tests = entry.get("tests")
        if not isinstance(tests, list) or not all(_text(t) for t in tests):
            raise Refusal(f"criteria entry {index} field tests must be a list of non-empty strings")
    for index, entry in enumerate(_entries(manifest, "docs_false", ("path", "sentence"))):
        entry["path"] = _path(entry.get("path"), f"docs_false entry {index} field path")
        if not _text(entry.get("sentence")):
            raise Refusal(f"docs_false entry {index} field sentence must be a non-empty string")
    for name in ("contract_sites", "invariant_sites"):
        _strings(manifest, name)
        manifest[name] = [_path(item, f"{name} entry {i}") for i, item in enumerate(manifest[name])]
    if "after_merge" in manifest:
        _strings(manifest, "after_merge")
    return manifest


def plan_checks(manifest: dict, paths_at_base: set[str]) -> list[str]:
    failures = []
    for entry in manifest["files"]:
        path, exists = entry["path"], entry["path"] in paths_at_base
        if entry["change"] == "modify" and not exists:
            failures.append(f"{path} is to be modified but does not exist at base")
        if entry["change"] == "create" and exists:
            failures.append(f"{path} is to be created but already exists at base")
    for entry in manifest["criteria"]:
        if not entry["tests"]:
            failures.append(f"criterion {entry['text']!r} has no test")
    for entry in manifest["docs_false"]:
        if entry["path"] not in paths_at_base:
            failures.append(f"{entry['path']} is named as a false doc but does not exist at base")
    return failures


def version(plan: bytes, manifest: dict) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(plan + b"\0" + canonical).hexdigest()
