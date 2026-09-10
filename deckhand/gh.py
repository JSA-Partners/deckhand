"""Thin wrapper over the GitHub CLI. Every function raises GhError with gh's own stderr on failure."""

from __future__ import annotations

import functools
import json
import subprocess
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:  # a runtime import would be circular: config resolves the project through this module
    from deckhand.config import Settings

# The token every project query uses where the owner root goes; `owner_query` fills it in.
OWNER_ROOT = "OWNER_ROOT"
OWNER_FIELDS = {"Organization": "organization", "User": "user"}

FIELDS_QUERY = (
    "query($owner:String!,$number:Int!){ OWNER_ROOT(login:$owner){ projectV2(number:$number){ "
    "fields(first:50){ nodes{ ... on ProjectV2FieldCommon{ id name dataType } "
    "... on ProjectV2SingleSelectField{ options{ id name color } } } } } } }"
)

LINKED_QUERY = (
    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){"
    "projectsV2(first:20){nodes{number title owner{__typename "
    "... on Organization{login} ... on User{login}}}}}}"
)


class GhError(Exception):
    """gh exited non-zero; the message is gh's stderr."""


def run(*args: str, stdin: str | None = None) -> str:
    """Run `gh <args>` and return stdout; raise GhError with stderr on failure."""
    try:
        result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise GhError("the GitHub CLI (gh) is not installed or not on PATH") from error
    if result.returncode != 0:
        raise GhError(result.stderr.strip() or f"gh {' '.join(args)} failed with status {result.returncode}")
    return result.stdout


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as error:
        raise GhError("gh returned no parseable JSON") from error


def json_out(*args: str, stdin: str | None = None) -> Any:
    return _parse_json(run(*args, stdin=stdin))


def _field(key: str, value: Any) -> list[str]:
    """A typed `-f`/`-F` pair for a GraphQL or REST variable."""
    if value is None:
        return ["-F", f"{key}=null"]
    if isinstance(value, bool):
        return ["-F", f"{key}={'true' if value else 'false'}"]
    if isinstance(value, int):
        return ["-F", f"{key}={value}"]
    return ["-f", f"{key}={value}"]


def graphql(query: str, variables: dict[str, Any] | None = None, paginate: bool = False) -> list[Any]:
    """Run a GraphQL query; return one parsed page per element (paginate needs a $endCursor variable)."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in (variables or {}).items():
        args += _field(key, value)
    if paginate:
        return _parse_json(run(*args, "--paginate", "--slurp"))
    return [_parse_json(run(*args))]


def graphql_json(query: str, variables: dict[str, Any]) -> Any:
    """A GraphQL call whose variables are not all scalars, sent as one JSON document on stdin."""
    payload = json.dumps({"query": query, "variables": variables})
    return _parse_json(run("api", "graphql", "--input", "-", stdin=payload))


def paginated(*args: str) -> list[Any]:
    """Run `gh api <args> --paginate --slurp` against a REST list endpoint; return the flattened items."""
    pages = _parse_json(run("api", *args, "--paginate", "--slurp"))
    items: list[Any] = []
    for page in pages:
        if not isinstance(page, list):
            endpoint = f"gh api {' '.join(args)}" if args else "gh api"
            raise GhError(f"expected a list page from {endpoint} --paginate --slurp, got an object")
        items.extend(page)
    return items


def split_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1] or "/" in parts[1] or parts[1] in (".", ".."):
        raise GhError(f"expected OWNER/REPO, got {repo!r}")
    return parts[0], parts[1]


NO_REPOSITORY = "not inside a git repository; run deckhand from the story's repository"


@functools.lru_cache(maxsize=1)
def repo_slug() -> str:
    """`owner/name` of the repository the working directory is in; the one place every command starts.

    gh answers from the checkout, so outside one it fails with git's own words; those are turned into
    the one rule a person can act on, because every command that follows would fail the same way.
    """
    try:
        return json_out("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
    except GhError as error:
        if "not a git repository" in str(error):
            raise GhError(NO_REPOSITORY) from error
        raise


class LinkedProject(NamedTuple):
    """One project the repository is linked to on GitHub."""

    owner: str
    owner_type: str
    number: int
    title: str

    def __str__(self) -> str:
        """`owner #number title`, on one line: a project title may hold newlines."""
        return " ".join(f"{self.owner} #{self.number} {self.title}".split())


def owner_field(owner_type: str) -> str:
    """The GraphQL root field for a project owner: `organization` or `user`."""
    field_name = OWNER_FIELDS.get(owner_type)
    if field_name is None:
        raise GhError(f"unknown project owner type: {owner_type!r}")
    return field_name


def owner_query(query: str, owner_type: str) -> str:
    """`query` with its OWNER_ROOT token replaced by the root field the owner type needs."""
    if OWNER_ROOT not in query:
        raise AssertionError(f"the query has no {OWNER_ROOT} token to fill in: {query}")
    return query.replace(OWNER_ROOT, owner_field(owner_type))


def _checked_owner_type(number: int, owner_type: object) -> str:
    if not isinstance(owner_type, str) or owner_type not in OWNER_FIELDS:
        raise GhError(f"project #{number} has an owner type deckhand cannot query: {owner_type!r}")
    return owner_type


def linked_projects(repo: str) -> list[LinkedProject]:
    """Every project GitHub has linked to `repo`, first page of 20."""
    o, n = split_repo(repo)
    data = graphql(LINKED_QUERY, {"owner": o, "name": n})[0]
    repository = (data.get("data") or {}).get("repository")
    if repository is None:
        raise GhError(f"no such repository: {repo}")
    projects = []
    for node in (repository.get("projectsV2") or {}).get("nodes") or []:
        project_owner = node.get("owner") or {}
        number = node["number"]
        projects.append(
            LinkedProject(
                owner=project_owner.get("login") or "",
                owner_type=_checked_owner_type(number, project_owner.get("__typename")),
                number=number,
                title=node.get("title") or "",
            )
        )
    return projects


def project_lookup(repo: str, number: int) -> LinkedProject:
    """Project `number` under the repository's own owner, for an override that names an unlinked project."""
    o = split_repo(repo)[0]
    data = json_out("project", "view", str(number), "--owner", o, "--format", "json")
    project_owner = data.get("owner") or {}
    return LinkedProject(
        owner=project_owner.get("login") or o,
        owner_type=_checked_owner_type(number, project_owner.get("type")),
        number=number,
        title=data.get("title") or "",
    )


def project_id(settings: Settings) -> str:
    return json_out("project", "view", str(settings.project), "--owner", settings.owner, "--format", "json")["id"]


def field_list(settings: Settings) -> list[dict[str, Any]]:
    """Every field on the configured project, as `gh project field-list` reports it."""
    return json_out("project", "field-list", str(settings.project), "--owner", settings.owner, "--format", "json")[
        "fields"
    ]


def project_fields(settings: Settings) -> list[dict[str, Any]]:
    """Every field on the configured project as `{id, name, dataType, options}`, first page of 50.

    `field-list` reports a field's GraphQL class, and only the `dataType` the API itself uses says
    which fields a person made and which GitHub built in, so this reads them through GraphQL; the
    options of a single select carry their colors here, which `field-list` does not report.
    """
    query = owner_query(FIELDS_QUERY, settings.owner_type)
    data = graphql(query, {"owner": settings.owner, "number": settings.project})[0]
    root = (data.get("data") or {}).get(owner_field(settings.owner_type)) or {}
    nodes = ((root.get("projectV2") or {}).get("fields") or {}).get("nodes") or []
    return [
        {
            "id": node.get("id"),
            "name": node.get("name") or "",
            "dataType": node.get("dataType") or "",
            "options": node.get("options") or [],
        }
        for node in nodes
        if node and node.get("id")
    ]


def create_single_select(settings: Settings, name: str, options: list[dict[str, str]]) -> None:
    """Create a single-select field with named and colored options; colors need GraphQL."""
    mutation = (
        "mutation($project:ID!,$name:String!,$options:[ProjectV2SingleSelectFieldOptionInput!]!){ "
        "createProjectV2Field(input:{projectId:$project,dataType:SINGLE_SELECT,name:$name,"
        "singleSelectOptions:$options}){ projectV2Field{ ... on ProjectV2SingleSelectField{ id } } } }"
    )
    graphql_json(mutation, {"project": project_id(settings), "name": name, "options": options})


def update_single_select(settings: Settings, field_id: str, options: list[dict[str, str]]) -> None:
    """Replace a single-select field's options; an option keeps its values only when sent with its id."""
    mutation = (
        "mutation($field:ID!,$options:[ProjectV2SingleSelectFieldOptionInput!]!){ "
        "updateProjectV2Field(input:{fieldId:$field,singleSelectOptions:$options}){ "
        "projectV2Field{ ... on ProjectV2SingleSelectField{ id } } } }"
    )
    graphql_json(mutation, {"field": field_id, "options": options})


def merge_settings(repo: str) -> dict[str, Any]:
    """The repository object of the REST API, which is where the merge settings are reported."""
    o, r = split_repo(repo)
    data = json_out("api", f"repos/{o}/{r}")
    if not isinstance(data, dict):
        raise GhError(f"expected a repository object from gh api repos/{o}/{r}")
    return data


def field(settings: Settings, name: str) -> dict[str, Any]:
    for item in field_list(settings):
        if item["name"] == name:
            return item
    raise GhError(f"no such project field: {name}")


def item_id(settings: Settings, repo: str, number: int) -> str | None:
    """The issue's item id on the configured project, or None when it is not on the board."""
    query = (
        "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n)"
        "{projectItems(first:20){nodes{id project{number}}}}}}"
    )
    o, r = split_repo(repo)
    issue = graphql(query, {"o": o, "r": r, "n": number})[0]["data"]["repository"]["issue"]
    if issue is None:
        raise GhError(f"issue #{number} does not exist in {repo}")
    for node in issue["projectItems"]["nodes"]:
        if node["project"]["number"] == settings.project:
            return node["id"]
    return None


def issue_id(repo: str, number: int) -> int:
    """The numeric database id the dependencies API expects."""
    o, r = split_repo(repo)
    return json_out("api", f"repos/{o}/{r}/issues/{number}")["id"]
