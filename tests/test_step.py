from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

import pytest

from deckhand import cli
from deckhand.config import Settings
from deckhand.step import PLUGIN_ROOT, Refusal, branch_for, draft_path, indented, read_draft, refuse_git, step

MODULE = "deckhand_demo_step"


@pytest.fixture
def demo(registry, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A stand-in step module, so `step()` has somewhere real to look `context` up by name."""
    module = types.ModuleType(MODULE)
    monkeypatch.setitem(sys.modules, MODULE, module)
    return module


def register(
    module,
    context,
    apply,
    *,
    configure_apply=lambda parser: None,
    issue_bound=True,
    configure_context=None,
):
    """Register `demo` the way a step module does: `context` beside the decorated `apply`."""
    module.context = context
    apply.__module__ = module.__name__
    return step("demo", configure_apply, issue_bound=issue_bound, configure_context=configure_context)(apply)


def unused(args: argparse.Namespace) -> int:
    """Demo step."""
    raise AssertionError("this verb should not have run")


def test_context_prints_one_line_and_returns_0_when_it_raises(demo, capsys):
    def context(args):
        raise RuntimeError("boom")

    register(demo, context, unused)

    assert cli.main(["demo", "context", "5"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "(deckhand demo context failed: boom. Say what could not be read and stop.)\n"
    assert captured.err == ""


def test_context_returns_its_own_value(demo, capsys):
    def context(args):
        print(f"issue {args.issue}")
        return 3

    register(demo, context, unused)

    assert cli.main(["demo", "context", "5"]) == 3
    assert capsys.readouterr().out == "issue 5\n"


def test_apply_refusal_is_one_line_on_stderr_at_exit_1(demo, capsys):
    def apply(args):
        """Demo step."""
        raise Refusal("nope")

    register(demo, unused, apply)

    assert cli.main(["demo", "apply", "5"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "deckhand demo apply: nope\n"
    assert captured.out == ""


def test_apply_propagates_any_other_exception_to_cli_main(demo, capsys):
    def apply(args):
        """Demo step."""
        raise ValueError("something broke")

    register(demo, unused, apply)

    assert cli.main(["demo", "apply", "5"]) == 1
    assert capsys.readouterr().err == "deckhand demo: something broke\n"


def test_apply_without_the_issue_exits_2(demo):
    def apply(args):
        """Demo step."""
        return 0

    register(demo, unused, apply)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["demo", "apply"])
    assert excinfo.value.code == 2


def test_context_without_a_verb_exits_2(demo):
    register(demo, unused, unused)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["demo"])
    assert excinfo.value.code == 2


def test_a_step_that_is_not_issue_bound_takes_no_issue(demo, capsys):
    def context(args):
        print("no issue needed")
        return 0

    register(demo, context, unused, issue_bound=False)

    assert cli.main(["demo", "context"]) == 0
    assert capsys.readouterr().out == "no issue needed\n"
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["demo", "context", "5"])
    assert excinfo.value.code == 2


def test_configure_context_arguments_reach_args(demo, capsys):
    def context(args):
        print(f"{args.issue} {args.source}")
        return 0

    register(demo, context, unused, configure_context=lambda p: p.add_argument("source", nargs="?"))

    assert cli.main(["demo", "context", "5", "a-file.md"]) == 0
    assert capsys.readouterr().out == "5 a-file.md\n"
    assert cli.main(["demo", "context", "5"]) == 0
    assert capsys.readouterr().out == "5 None\n"


def test_a_step_without_configure_context_takes_the_issue_alone(demo):
    register(demo, unused, unused)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["demo", "context", "5", "a-file.md"])
    assert excinfo.value.code == 2


def test_configure_apply_flags_reach_args(demo, capsys):
    def apply(args):
        """Demo step."""
        print(f"{args.issue} {args.note}")
        return 0

    def configure_apply(parser):
        parser.add_argument("--note", required=True)

    register(demo, unused, apply, configure_apply=configure_apply)

    assert cli.main(["demo", "apply", "5", "--note", "one line"]) == 0
    assert capsys.readouterr().out == "5 one line\n"


def test_command_help_is_the_apply_docstrings_first_line(demo, capsys):
    def apply(args):
        """Do the demo step.

        More detail that only shows up in the full description.
        """
        return 0

    register(demo, unused, apply)

    assert "Do the demo step." in cli.build_parser().format_help()


def test_draft_path_makes_the_parents_but_not_the_file(tmp_path: Path):
    settings = Settings(cache=tmp_path / "cache")

    path = draft_path(settings, "acme/widgets", "5/draft.md")

    assert path == tmp_path / "cache" / "widgets" / "5" / "draft.md"
    assert path.parent.is_dir()
    assert not path.exists()


def test_context_swallows_a_refusal_too(demo, capsys):
    def context(args):
        raise Refusal("not yet")

    register(demo, context, unused)

    assert cli.main(["demo", "context", "5"]) == 0
    assert "not yet" in capsys.readouterr().out


def test_a_step_without_context_is_a_real_error(demo, capsys):
    def apply(args):
        """Demo step."""
        return 0

    apply.__module__ = demo.__name__
    step("demo", lambda parser: None)(apply)  # no context defined on the module

    assert cli.main(["demo", "context", "5"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "context" in captured.err


def test_a_none_return_is_exit_0(demo):
    def context(args):
        return None

    def apply(args):
        """Demo step."""
        return None

    register(demo, context, apply)

    assert cli.main(["demo", "context", "5"]) == 0
    assert cli.main(["demo", "apply", "5"]) == 0


def test_a_step_flag_cannot_route_the_verb(demo, capsys):
    def context(args):
        print("context ran")
        return 0

    def apply(args):
        """Demo step."""
        print("apply ran")
        return 0

    register(demo, context, apply, configure_apply=lambda p: p.add_argument("--verb", default="x"))

    assert cli.main(["demo", "apply", "5", "--verb", "context"]) == 0
    assert capsys.readouterr().out == "apply ran\n"


def test_configure_apply_cannot_redefine_the_issue_argument(demo, capsys):
    def apply(args):
        """Demo step."""
        return 0

    register(demo, unused, apply, configure_apply=lambda p: p.add_argument("issue"))

    assert cli.main(["demo", "apply", "5"]) == 1  # a wiring bug, reported by cli.main as one line
    assert "issue argument" in capsys.readouterr().err


def test_registering_a_command_twice_is_an_error(demo):
    def apply(args):
        """Demo step."""
        return 0

    register(demo, unused, apply)

    with pytest.raises(ValueError, match="registered twice"):
        register(demo, unused, apply)


def test_draft_path_refuses_a_name_that_escapes_the_cache(tmp_path):
    settings = Settings(cache=tmp_path / "cache")

    with pytest.raises(ValueError, match="escapes the cache"):
        draft_path(settings, "acme/widgets", "../../outside.md")
    assert not (tmp_path / "outside.md").exists()


# --- read_draft -------------------------------------------------------------


def test_read_draft_returns_the_text(tmp_path: Path):
    path = tmp_path / "draft.md"
    path.write_text("### Task 1\n", encoding="utf-8")

    assert read_draft(path) == "### Task 1\n"


def test_read_draft_refuses_a_missing_file(tmp_path: Path):
    with pytest.raises(Refusal, match="cannot read "):
        read_draft(tmp_path / "nope.md")


def test_read_draft_refuses_a_directory(tmp_path: Path):
    with pytest.raises(Refusal, match="cannot read "):
        read_draft(tmp_path)


def test_read_draft_reads_past_a_byte_order_mark(tmp_path: Path):
    path = tmp_path / "draft.md"
    path.write_bytes("\ufeffNothing found.\n".encode("utf-8"))

    assert read_draft(path) == "Nothing found.\n"


def test_read_draft_refuses_bytes_that_are_not_utf_8(tmp_path: Path):
    path = tmp_path / "draft.md"
    path.write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(Refusal, match="it is not valid UTF-8"):
        read_draft(path)


def test_indented_puts_two_spaces_in_front_of_every_line_that_has_one():
    assert indented(["one", "  ", "two"]) == ["  one", "  two"]


def test_indented_says_so_when_there_is_nothing_to_indent():
    assert indented([]) == ["  none"]
    assert indented("".splitlines(), "none detected") == ["  none detected"]


def test_refuse_git_returns_what_git_printed(repo):
    assert refuse_git("rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_refuse_git_turns_a_git_failure_into_a_refusal(repo):
    with pytest.raises(Refusal) as raised:
        refuse_git("rev-parse", "--verify", "no-such-ref")

    assert "Needed a single revision" in str(raised.value)


# --- branch_for --------------------------------------------------------------


def test_branch_for_builds_the_story_branch(settings):
    assert branch_for(settings, "feat", "Guest users see only their granted collections", 248) == (
        "feat/248-guest-users-see-only"
    )


def test_branch_for_names_the_command_that_gives_a_story_its_kind(settings):
    with pytest.raises(ValueError, match=r"#248 has no Kind; run /deckhand:next 248"):
        branch_for(settings, None, "Guest users", 248)


def test_branch_for_refuses_a_title_with_no_slug_in_it(settings):
    with pytest.raises(ValueError, match="yields no usable slug"):
        branch_for(settings, "feat", "***", 248)


# --- PLUGIN_ROOT -------------------------------------------------------------


def test_the_plugin_root_holds_the_skills_and_the_package():
    assert (PLUGIN_ROOT / "skills").is_dir()
    assert (PLUGIN_ROOT / "deckhand" / "step.py").is_file()
