from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from deckhand import answers, cli


def run(monkeypatch: pytest.MonkeyPatch, args: list[str], payload: object) -> int:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return cli.main(["hook", "apply", *args])


def asked(header: str, labels: list[str]) -> dict:
    return {
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "Q?", "header": header, "options": [{"label": x} for x in labels]}]},
    }


def test_hook_answer_records(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    payload = {
        "session_id": "s",
        "tool_use_id": "t",
        "tool_response": {"questions": [{"question": "Q?", "header": "dh abcd1234"}], "answers": {"Q?": "A"}},
    }
    assert run(monkeypatch, ["answer"], payload) == 0
    assert answers.find("abcd1234", "Q?", tmp_path).answer == "A"


def test_hook_answer_never_fails_the_session(monkeypatch, tmp_path: Path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(blocker / "state"))
    payload = {
        "tool_response": {
            "questions": [{"question": "SECRET?", "header": "dh abcd1234"}],
            "answers": {"SECRET?": "hunter2"},
        }
    }
    assert run(monkeypatch, ["answer"], payload) == 0
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert "hunter2" not in err and "SECRET" not in err
    assert run(monkeypatch, ["answer"], "not json") == 0


def test_hook_ask_denies_recommended_in_deckhand_questions(monkeypatch, capsys):
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide (Recommended)", "Narrow"])) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"]


def test_hook_ask_allows_clean_deckhand_question(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Wide", "Narrow"])
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert capsys.readouterr().out == ""


def test_hook_ask_ignores_other_questions(monkeypatch, capsys):
    assert run(monkeypatch, ["ask"], asked("Scope", ["Wide (Recommended)"])) == 0
    assert capsys.readouterr().out == ""


def pending_for(state: Path, nonce: str, question: str, options: list[str]) -> None:
    folder = state / "pending"
    folder.mkdir(parents=True)
    (folder / f"{nonce}.json").write_text(
        json.dumps(
            {
                "nonce": nonce,
                "kind": "feature-approval",
                "subject": "s",
                "version": "v",
                "stories": ["s1"],
                "question": question,
                "options": options,
                "descriptions": [],
            }
        )
    )


def test_hook_ask_denies_deckhand_question_without_pending(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("question,labels", [("Other?", ["Wide", "Narrow"]), ("Q?", ["Wide", "Wider"])])
def test_hook_ask_denies_a_question_that_differs_from_pending(monkeypatch, tmp_path: Path, capsys, question, labels):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", question, ["Wide", "Narrow"])
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", labels)) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_ask_allows_the_pending_question(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Wide", "Narrow"])
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert capsys.readouterr().out == ""


def test_hook_ask_ignores_a_malformed_nonce_with_one_stderr_line(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    (tmp_path / "x.json").write_text("{}")
    assert run(monkeypatch, ["ask"], asked("dh ../x", ["Wide", "Narrow"])) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1


def deny(capsys) -> bool:
    out = capsys.readouterr().out
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "option",
    [
        {"label": "Wide", "description": "The wider scope (Recommended)"},
        {"label": "Wide", "description": "Recommended for most teams"},
        {"label": "Recommended", "description": "contract: s"},
    ],
)
def test_hook_ask_denies_recommended_in_a_label_or_description(monkeypatch, tmp_path: Path, capsys, option):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", [option["label"], "Narrow"])
    payload = asked("dh abcd1234", [option["label"], "Narrow"])
    payload["tool_input"]["questions"][0]["options"][0] = option
    assert run(monkeypatch, ["ask"], payload) == 0
    assert deny(capsys)


def test_hook_ask_denies_a_deckhand_question_that_carries_answers(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Wide", "Narrow"])
    payload = asked("dh abcd1234", ["Wide", "Narrow"])
    payload["tool_input"]["answers"] = {"Q?": "Wide"}
    assert run(monkeypatch, ["ask"], payload) == 0
    assert deny(capsys)


def test_recommended_in_a_fixed_description_still_denies(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Approve", "Not yet"])
    options = [{"label": "Approve", "description": "ACME-1: recommended"}, {"label": "Not yet"}]
    payload = asked("dh abcd1234", [])
    payload["tool_input"]["questions"][0]["options"] = options
    assert run(monkeypatch, ["ask"], payload) == 0
    assert deny(capsys)


def hook_with(monkeypatch: pytest.MonkeyPatch, store, args: list[str], payload: object, captain=None) -> int:
    from deckhand.steps.hook import Hook

    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    Hook(store, captain).apply(args)
    return 0


GAP = {
    "hook_event_name": "PermissionDenied",
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr list"},
    "reason": "not allowed",
}


def test_session_hook_prints_captain_context_only_for_captain(monkeypatch, capsys, tmp_path: Path):
    from deckhand.steps.captain import Captain
    from tests.test_step_captain import NOW, engine_wrote, owned

    engine_wrote(owned())
    Captain(clock=lambda: NOW).context([])
    want = capsys.readouterr().out
    assert want.splitlines()[-1] == "engine: running"
    store = owned()
    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    hook_with(monkeypatch, store, ["session"], {"hook_event_name": "SessionStart"}, Captain(clock=lambda: NOW))
    out = capsys.readouterr()
    assert (out.out, out.err) == (want, "")
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    hook_with(monkeypatch, store, ["session"], {}, Captain(clock=lambda: NOW))
    assert capsys.readouterr().out == ""
    monkeypatch.delenv("DECKHAND_ROLE")
    hook_with(monkeypatch, store, ["session"], {}, Captain(clock=lambda: NOW))
    assert capsys.readouterr().out == ""


class Raising:
    def __getattr__(self, name: str):
        raise AssertionError(f"the store was called: {name}")


def test_a_hand_session_start_prints_the_pointer_and_reads_no_linear(capsys, monkeypatch):
    from deckhand import spec

    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", "story")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    from deckhand.steps.hook import Hook

    Hook(Raising()).apply(["session"])
    out = capsys.readouterr()
    assert (out.out, out.err) == (spec.pointer("ACME-1") + "\n", "")


def test_a_draft_session_start_points_at_the_draft_context(capsys, monkeypatch):
    from deckhand import spec

    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", "draft")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    from deckhand.steps.hook import Hook

    Hook(Raising()).apply(["session"])
    out = capsys.readouterr()
    assert (out.out, out.err) == (spec.draft_pointer("ACME-1") + "\n", "")


@pytest.mark.parametrize("kind", ["", "unknown"])
def test_a_hand_of_no_known_kind_prints_nothing(capsys, monkeypatch, kind):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", kind)
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    from deckhand.steps.hook import Hook

    Hook(Raising()).apply(["session"])
    assert capsys.readouterr().out == ""


def test_a_spike_session_start_prints_nothing(capsys, monkeypatch):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", "spike")
    monkeypatch.setenv("DECKHAND_SPIKE", "ACME-1")
    monkeypatch.delenv("DECKHAND_STORY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    from deckhand.steps.hook import Hook

    Hook(Raising()).apply(["session"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


def test_permission_gap_recorded_for_hand(monkeypatch, capsys, tmp_path: Path):
    from deckhand import gaps
    from tests.test_step_decide import feature_store

    (tmp_path / "isolated-config").mkdir()
    (tmp_path / "isolated-config" / "config.toml").write_text('team_key = "ACME"\n')
    store = feature_store()
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    hook_with(monkeypatch, store, ["permission"], GAP)
    [held] = store.records("s1")
    want = gaps.record("s1", "PermissionDenied", "Bash", "gh pr list", "not allowed")
    assert held.to_json() == want.to_json()
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert len(store.records("s1")) == 1
    hook_with(monkeypatch, store, ["permission"], {"hook_event_name": "PermissionRequest"})
    assert len(store.records("s1")) == 2
    assert capsys.readouterr().err == ""


def test_permission_hook_silent_outside_hands(monkeypatch, capsys):
    from tests.test_step_decide import feature_store

    store = feature_store()
    hook_with(monkeypatch, store, ["permission"], GAP)
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    hook_with(monkeypatch, store, ["permission"], GAP)
    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert store.records("s1") == []
    assert capsys.readouterr().out == ""


CAPTAIN_DENIAL = {
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {
            "behavior": "deny",
            "message": "the captain runs deckhand commands only; check the fact with Read or Grep, or ask a spike",
        },
    }
}


def test_a_captain_permission_request_is_denied_except_ask(monkeypatch, capsys):
    from tests.test_step_decide import feature_store

    store = feature_store()
    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    for tool, input in (("Bash", {"command": "git log"}), ("Write", {"file_path": "/x"}), ("WebFetch", {})):
        hook_with(
            monkeypatch,
            store,
            ["permission"],
            {"hook_event_name": "PermissionRequest", "tool_name": tool, "tool_input": input},
        )
        assert json.loads(capsys.readouterr().out) == CAPTAIN_DENIAL
    asked_request = {"hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion", "tool_input": {}}
    hook_with(monkeypatch, store, ["permission"], asked_request)
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert capsys.readouterr().out == ""
    assert store.records("s1") == []


@pytest.mark.parametrize("payload", ["not json", [], ""])
def test_a_captain_permission_request_that_cannot_be_read_is_denied(monkeypatch, capsys, payload):
    from tests.test_step_decide import feature_store

    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    hook_with(monkeypatch, feature_store(), ["permission"], payload)
    assert json.loads(capsys.readouterr().out) == CAPTAIN_DENIAL


def test_a_hand_permission_request_is_unchanged(monkeypatch, capsys, tmp_path: Path):
    from deckhand import gaps
    from tests.test_step_decide import feature_store

    (tmp_path / "isolated-config").mkdir()
    (tmp_path / "isolated-config" / "config.toml").write_text('team_key = "ACME"\n')
    store = feature_store()
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    request = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "git log"}}
    hook_with(monkeypatch, store, ["permission"], request)
    assert capsys.readouterr().out == ""
    [held] = store.records("s1")
    assert held.to_json() == gaps.record("s1", "PermissionRequest", "Bash", "git log", "").to_json()


def test_permission_hook_never_decides(monkeypatch, capsys, tmp_path: Path):
    from tests.test_step_decide import feature_store

    (tmp_path / "isolated-config").mkdir()
    (tmp_path / "isolated-config" / "config.toml").write_text('team_key = "ACME"\n')
    store = feature_store()
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert capsys.readouterr().out == ""
    hook_with(monkeypatch, store, ["permission"], "not json")
    hook_with(monkeypatch, store, ["permission"], [])
    out = capsys.readouterr()
    assert out.out == ""
    assert len(out.err.splitlines()) == 2
    monkeypatch.setenv("DECKHAND_STORY", "ACME-99")
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert capsys.readouterr().out == ""


def test_hooks_json_uses_the_full_path_form():
    from tests.conftest import ROOT

    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    want = {
        "SessionStart": ("startup|resume|compact|clear", "session"),
        "PermissionRequest": ("*", "permission"),
        "PermissionDenied": ("*", "permission"),
    }
    for event, (matcher, action) in want.items():
        [entry] = hooks[event]
        assert entry["matcher"] == matcher
        [command] = entry["hooks"]
        assert command["command"] == f'"${{CLAUDE_PLUGIN_ROOT}}/bin/deckhand" hook apply {action}'
    pre = {entry["matcher"]: [h["command"] for h in entry["hooks"]] for entry in hooks["PreToolUse"]}
    assert pre["Bash"] == ['"${CLAUDE_PLUGIN_ROOT}/bin/deckhand" hook apply bash']


def test_a_turn_that_ends_on_an_api_error_marks_its_end_as_a_stop_does():
    from tests.conftest import ROOT

    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert hooks.get("StopFailure") == hooks["Stop"]


def test_permission_gap_recorded_after_the_feature_is_released(monkeypatch, tmp_path: Path):
    from tests.test_step_decide import feature_store

    (tmp_path / "isolated-config").mkdir()
    (tmp_path / "isolated-config" / "config.toml").write_text('team_key = "ACME"\n')
    store = feature_store()
    store.set_issue_state("s1", "Done")
    store.set_issue_state("s2", "Done")
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert len(store.records("s1")) == 1


def test_permission_gap_recorded_once_the_next_feature_opens(monkeypatch, tmp_path: Path):
    from tests.test_step_decide import feature_store, released

    (tmp_path / "isolated-config").mkdir()
    (tmp_path / "isolated-config" / "config.toml").write_text('team_key = "ACME"\n')
    store = released(feature_store())
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    hook_with(monkeypatch, store, ["permission"], GAP)
    assert len(store.records("s1")) == 1


def turn_ended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str | None) -> str | None:
    from deckhand.steps.hook import Hook
    from deckhand.store.state import LocalState

    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    if role is None:
        monkeypatch.delenv("DECKHAND_ROLE", raising=False)
    else:
        monkeypatch.setenv("DECKHAND_ROLE", role)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with LocalState(tmp_path / "deckhand.db") as rows:
        rows.want("hand-ACME-1", "ACME-1", "s1", "story", str(tmp_path))
    Hook(Raising()).apply(["stop"])
    with LocalState(tmp_path / "deckhand.db") as rows:
        return rows.hands()["hand-ACME-1"].turn_ended


def test_hook_stop_marks_the_turn_end_for_a_hand(monkeypatch, tmp_path: Path, capsys):
    assert turn_ended(tmp_path, monkeypatch, "hand") is not None
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize("role", ["captain", None])
def test_hook_stop_outside_a_hand_writes_nothing(monkeypatch, tmp_path: Path, capsys, role):
    assert turn_ended(tmp_path, monkeypatch, role) is None
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


def test_a_hand_may_apply_the_stop_hook(monkeypatch):
    from deckhand.step import hand_may_apply

    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    assert hand_may_apply("hook", ["stop"])


def test_hook_stop_marks_the_turn_end_for_a_spike_hand(monkeypatch, tmp_path: Path, capsys):
    from deckhand.steps.hook import Hook
    from deckhand.store.state import LocalState

    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", "spike")
    monkeypatch.setenv("DECKHAND_SPIKE", "a1b2c3d4e5f6")
    monkeypatch.delenv("DECKHAND_STORY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with LocalState(tmp_path / "deckhand.db") as rows:
        rows.want("spike-a1b2c3d4e5f6", "a1b2c3d4e5f6", "p1", "spike", str(tmp_path))
    Hook(Raising()).apply(["stop"])
    with LocalState(tmp_path / "deckhand.db") as rows:
        assert rows.hands()["spike-a1b2c3d4e5f6"].turn_ended is not None
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize(("source", "kept"), [("startup", False), ("resume", True), ("compact", True), ("clear", True)])
def test_a_captain_startup_clears_the_hold_and_a_compact_keeps_it(tmp_path: Path, monkeypatch, capsys, source, kept):
    from deckhand.steps.captain import Captain
    from deckhand.store.local import default_path
    from deckhand.store.state import LocalState
    from deckhand.watch import HOLD

    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    Captain().apply(["hold"])
    capsys.readouterr()
    context = []
    stub = type("Stub", (), {"context": lambda self, args: context.append(args)})()
    hook_with(monkeypatch, Raising(), ["session"], {"hook_event_name": "SessionStart", "source": source}, stub)
    with LocalState(default_path()) as rows:
        assert (rows.get(HOLD) is not None) is kept
    assert context == [[]]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("payload", ["", "not json", "[1]"])
def test_a_captain_session_with_unreadable_input_still_prints_its_context_as_a_startup(
    tmp_path: Path, monkeypatch, capsys, payload
):
    from deckhand.steps.captain import Captain
    from deckhand.store.local import default_path
    from deckhand.store.state import LocalState
    from deckhand.watch import HOLD

    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    Captain().apply(["hold"])
    capsys.readouterr()
    context = []
    stub = type("Stub", (), {"context": lambda self, args: context.append(args)})()
    hook_with(monkeypatch, Raising(), ["session"], payload, stub)
    with LocalState(default_path()) as rows:
        assert rows.get(HOLD) is None
    assert context == [[]]
    assert capsys.readouterr().err == ""


BASH_DENIAL = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "the captain runs deckhand commands only: deckhand, then words of letters, digits and _./:=@,+%-,"
            " 'single-quoted' text or \"double-quoted\" text without $ ` \\ !, separated by spaces;"
            " check the fact with Read or Grep, or ask a spike"
        ),
    }
}


def bash(command: object) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command",
    [
        "deckhand captain context",
        "deckhand",
        'deckhand feature apply new --name "Help files" --weeks 1',
        'deckhand review apply X resolve --reason "a; b & c"',
        "deckhand decide context decision-item 'Search & filter'",
        "deckhand plan apply Facts --file /tmp/x/facts.md --drafted",
        'deckhand x "don\'t stop"',
        "deckhand plan context 'a $(b)'",
        'deckhand x \'say "hi"\' "a # * ? [b] ~ {c}"',
    ],
)
def test_a_captain_bare_deckhand_call_passes(monkeypatch, capsys, command):
    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    assert run(monkeypatch, ["bash"], bash(command)) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "command",
    [
        "deckhand captain context; ls",
        "deckhand next#;echo PWNED",
        "deckhand next # '\necho PWNED\n# '",
        "deckhand $'\\'' $(echo PWNED) '$'\\'''",
        'deckhand x "a $(b)"',
        'deckhand plan context "\\$(id)"',
        'deckhand x "hi!"',
        'deckhand x a"b"',
        "deckhand x 'a''b'",
        "deckhand plan context 'one\ntwo'",
        "deckhand x\techo",
        "deckhand x 'café'",
        "deckhand x ~/f",
        "deckhand x *",
        "deckhand x {a,b}",
        " deckhand captain context",
        "deckhand captain context ",
        "deckhandx",
        "",
        "deckhand x;rm y",
        "deckhand x | tee",
        "deckhand x > f",
        'deckhand "$(id)"',
        "deckhand `id`",
        "deckhand x && y",
        "deckhand 'unbalanced",
        'deckhand "a\\" \' $(id) \'"',
        'deckhand "one\ntwo"',
        "deckhand captain context && ls",
        "deckhand captain context || ls",
        "deckhand captain context & ls",
        "deckhand captain context | head",
        "deckhand captain context > out.txt",
        "deckhand stories apply --file < map.json",
        "deckhand plan context spike `cat key`",
        "deckhand plan context spike $(cat key)",
        "deckhand captain context\nls",
        "ls -R skills/feature",
        "uv run --no-project python -I -c 'print(1)'",
        "DECKHAND_ROLE=hand deckhand captain context",
        None,
        ["deckhand", "captain"],
    ],
)
def test_a_captain_bash_call_other_than_one_bare_deckhand_is_denied(monkeypatch, capsys, command):
    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    assert run(monkeypatch, ["bash"], bash(command)) == 0
    assert json.loads(capsys.readouterr().out) == BASH_DENIAL


@pytest.mark.parametrize("payload", ["not json", [], "", {"tool_name": "Bash"}])
def test_a_captain_bash_call_that_cannot_be_read_is_denied(monkeypatch, capsys, payload):
    monkeypatch.setenv("DECKHAND_ROLE", "captain")
    assert run(monkeypatch, ["bash"], payload) == 0
    assert json.loads(capsys.readouterr().out) == BASH_DENIAL


@pytest.mark.parametrize("role", ["hand", None])
@pytest.mark.parametrize("payload", [bash("ls -R skills; cat x | head"), bash("deckhand story context"), "not json"])
def test_the_bash_hook_leaves_a_hand_and_a_plain_session_alone(monkeypatch, capsys, role, payload):
    if role:
        monkeypatch.setenv("DECKHAND_ROLE", role)
    else:
        monkeypatch.delenv("DECKHAND_ROLE", raising=False)
    assert run(monkeypatch, ["bash"], payload) == 0
    assert capsys.readouterr().out == ""
