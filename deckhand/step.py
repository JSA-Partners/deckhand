"""One process step as one command: `deckhand <step> context [N]` and `deckhand <step> apply N ...`.

`context` prints what the model needs and never fails, so a skill can inject it without a broken
lookup swallowing the whole prompt. `apply` validates everything before it writes anything; a
refusal is one line at exit 1 with nothing written. A step whose write is git's or the editor's has
no `apply`, and is the same command with the one verb.

The helpers a context is built from live here too, so every step degrades the same way: `block`
prints one heading and whatever its filler returns, turning a failure into one line under that
heading and leaving the other blocks alone.
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from deckhand import config, gh, git, issue, naming, stub
from deckhand.cli import Configure, Handler, command
from deckhand.config import Settings

VERB = "_deckhand_verb"  # a private dest, so a step's own flags can never route the verb
MAIN = "main"  # the trunk every story branches from and returns to
ORIGIN_MAIN = f"origin/{MAIN}"  # what has landed on it, which is what every range is read against
# The checkout or installed plugin this package sits in, which is where the skill files are.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class Refusal(Exception):
    """One line the user reads; raised before any write."""


def issue_number(value: str) -> int:
    """An argparse `type=` that reports a friendly message for a non-numeric issue number."""
    if not (value.isascii() and value.isdigit()):
        raise argparse.ArgumentTypeError(f"issue number must be an integer, got {value!r}")
    return int(value)


def reason(error: Exception) -> str:
    """One line of an exception's message, for a block that could not be filled in."""
    return " ".join(str(error).split()) or error.__class__.__name__


def block(heading: str, fill: Callable[[], list[str]]) -> list[str]:
    """Print `heading` and the lines `fill` returns; a failure inside `fill` becomes one line.

    Each block stands on its own, so one lookup that fails never costs the model the rest of the
    prompt.
    """
    try:
        lines = fill()
    except Exception as error:
        lines = [f"  unavailable ({reason(error)})"]
    print(heading)
    for line in lines:
        print(line)
    return lines


def indented(lines: Iterable[str], empty: str = "none") -> list[str]:
    """Every line with something on it, indented two spaces; `  <empty>` when none of them has.

    Every block a step prints is read by a model against the heading above it, so an empty one says
    so in the same shape as a full one rather than leaving the heading to be read on its own.
    """
    return [f"  {line}" for line in lines if line.strip()] or [f"  {empty}"]


def trunk() -> str:
    """`origin/main` when the tracking ref is there, else local main; nothing here fetches.

    Every range a step reads is against the trunk a story branched from, and origin's is the one
    that says what has landed; a clone whose local main was never created still has to answer.
    """
    try:
        git.run("rev-parse", "--verify", "--quiet", ORIGIN_MAIN)
    except git.GitError:
        return MAIN
    return ORIGIN_MAIN


def refuse_git(*args: str) -> str:
    """git's stdout, or a `Refusal` carrying the one line git said about why it failed.

    For the git a step runs before it writes anything: git's own words are what the user has to act
    on, and a traceback would bury them.
    """
    try:
        return git.run(*args)
    except git.GitError as error:
        raise Refusal(str(error)) from error


def refuse_stub(number: int, body: str) -> None:
    """Refuse an issue that is still a stub; every step but `new` works on a story, and this is not one.

    The first thing an issue-bound step does with a body it has just read, so nothing downstream has
    to wonder whether the sections it wants are missing because the story is a stub.
    """
    if stub.is_stub(body):
        raise Refusal(f"#{number} is a stub; run /deckhand:new {number} first")


def branch_for(settings: Settings, kind: str | None, title: str, number: int) -> str:
    """The story's branch name; a `ValueError` when the board has no Kind or the title has no slug.

    One derivation for every caller, so the branch `next` looks for on origin is the branch `start`
    pushed there, and a story with no Kind is sent to the one command that can give it one.
    """
    if kind is None:
        raise ValueError(f"#{number} has no Kind; run /deckhand:next {number}")
    return naming.branch_name(settings, kind, number, title)


def blockers_block(repo: str, number: int) -> list[str]:
    """One line per open issue blocking `number`, or `  none`."""
    return [f"  {blocker}  {title}" for blocker, title in issue.blockers(repo, number)] or ["  none"]


def settings_or_error() -> Settings | Exception:
    """The settings, or the failure to load them, so the blocks that need them share one lookup."""
    try:
        return config.load()
    except Exception as error:
        return error


def usable[T](value: T | Exception) -> T:
    """`value`, or the failure to get it, raised inside the block that needs it."""
    if isinstance(value, Exception):
        raise value
    return value


def draft_path(settings: Settings, repo: str, name: str) -> Path:
    """The cache path `<cache>/<repo name>/<name>`, with parents made and the file left unwritten.

    `name` may hold subdirectories but must stay inside the cache; a name that escapes it is a bug
    in the caller, not a user error.
    """
    root = settings.cache.resolve()
    path = (root / gh.split_repo(repo)[1] / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"draft name escapes the cache: {name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def draft_line(label: str, name: str) -> str:
    """`<label>: <path>`, or `<label>: unavailable (<reason>)` when the path will not resolve.

    A context prints this before it prints anything the model has to write, and a repository or a
    cache that will not resolve costs one line, never the whole prompt.
    """
    try:
        return f"{label}: {draft_path(config.load(), gh.repo_slug(), name)}"
    except Exception as error:
        return f"{label}: unavailable ({reason(error)})"


def read_draft(path: Path) -> str:
    """The text of the file the model drafted; one it cannot hand over is a refusal, not a traceback.

    `utf-8-sig` because an editor's byte order mark would otherwise ride into the first field the
    caller parses and turn a good draft into a puzzling refusal about its first word.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise Refusal(f"cannot read '{path}': {error.strerror or error}") from error
    except UnicodeDecodeError as error:
        raise Refusal(f"cannot read '{path}': it is not valid UTF-8") from error


def step(
    name: str,
    configure_apply: Configure | None = None,
    issue_bound: bool = True,
    configure_context: Configure | None = None,
) -> Callable[[Handler], Handler]:
    """Register `deckhand <name> context [N]` and, with a `configure_apply`, `apply N ...` beside it.

    `configure_context` is for the step whose context takes more than an issue number; it runs
    against the context subparser once the issue positional is on it, so a step reads its own
    arguments in the order it declared them.

    Decorates a module-level pair: the decorated function is `apply(args) -> int`, and `context` is
    looked up by that name in the same module when the verb runs, so the two stay plain functions a
    test can call directly. Without `configure_apply` the step has the one verb, the decorated
    function is that `context`, and every command still reads `deckhand <name> <verb>`. `context`
    runs inside a catch-all that prints one line `(deckhand <name> context failed: <message>. ...)`
    and returns 0. `apply` lets a `Refusal` print `deckhand <name> apply: <message>` at exit 1; any
    other exception propagates to `cli.main`.
    """

    def configure(parser: argparse.ArgumentParser) -> None:
        metavar = "{context,apply}" if configure_apply is not None else "{context}"
        verbs = parser.add_subparsers(dest=VERB, required=True, metavar=metavar)
        context_parser = verbs.add_parser("context", help=f"Print what {name} needs; never fails.")
        verb_parsers = [context_parser]
        if configure_apply is not None:
            verb_parsers.append(verbs.add_parser("apply", help=f"Validate, then make the {name} changes."))
        if issue_bound:
            for verb_parser in verb_parsers:
                verb_parser.add_argument("issue", type=issue_number, help="the issue number")
        if configure_context is not None:
            configure_context(context_parser)
        if configure_apply is None:
            return
        apply_parser = verb_parsers[-1]
        before = {id(action) for action in apply_parser._actions}
        configure_apply(apply_parser)
        added = [a for a in apply_parser._actions if id(a) not in before]
        if issue_bound and any(a.dest == "issue" for a in added):
            raise ValueError(f"{name}: configure_apply must not redefine the issue argument")

    def register(verb: Handler) -> Handler:
        @functools.wraps(verb)  # the command's help is the decorated docstring, as cli._summary expects
        def handler(args: argparse.Namespace) -> int:
            if configure_apply is None or getattr(args, VERB) == "context":
                return _context(name, verb, args, issue_bound)
            return _apply(name, verb, args)

        command(name, configure)(handler)
        return verb

    return register


def _context(name: str, verb: Handler, args: argparse.Namespace, issue_bound: bool = True) -> int:
    """Run `context` from the module `verb` was defined in; nothing it raises escapes.

    The lookup itself sits outside the guard: a step module without `context` is a deckhand bug and
    must reach `cli.main` as an error, not be injected into a prompt as if it were content.

    The one line the guard prints ends with what the model does about it, and it is the same
    answer for every step: a context that could not be read is a fact nobody knows, and a step run
    on a guess writes the wrong thing to GitHub.
    """
    context = sys.modules[verb.__module__].context
    tail = "Say what could not be read and stop."
    try:
        return context(args) or 0
    except Exception as error:  # a skill injects this output; one line beats a failed prompt
        print(f"(deckhand {name} context failed: {error}. {tail})")
        return 0


def _apply(name: str, apply: Handler, args: argparse.Namespace) -> int:
    """Run `apply`; a refusal is one line at exit 1, every other error reaches cli.main."""
    try:
        return apply(args) or 0
    except Refusal as refusal:
        print(f"deckhand {name} apply: {refusal}", file=sys.stderr)
        return 1
