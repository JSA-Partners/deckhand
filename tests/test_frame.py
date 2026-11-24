from __future__ import annotations

from deckhand import board, fleet, frame
from tests.test_board import CAPTAIN, NOW, TODAY, G, at, item, sample, section, shown, stage, story, texts


def test_long_titles_end_in_an_ellipsis_and_every_row_fits():
    for cols in (60, 80, 100, 140):
        lines = shown(cols=cols)
        assert all(frame.cells(line.text) <= cols for line in lines)
    row = section(shown(cols=70), "working")[0]
    assert "Add a farewell banner to the g... branch review 2/3" in row


def test_wide_characters_count_two_cells():
    status = sample(items=[], stories=[story("ACME-15", "日本語" * 20, stage=stage("building", 1))])
    lines = shown(status, cols=80)
    assert all(frame.cells(line.text) <= 80 for line in lines)


def test_below_sixty_columns_the_title_drops():
    assert section(shown(cols=59), "needs you")[0].split() == ["!", "ACME-14", "sign-off", "14m"]
    assert "Print every greeting" in section(shown(cols=60), "needs you")[0]


def test_below_eight_rows_only_the_header_shows_and_it_carries_the_count():
    assert texts(shown(rows=7)) == [
        " ! 2 need you | question ACME-16 3m | updated 6s ago | Alpha | day 2 of 7 | slots 2/2 | captain idle"
    ]
    assert len(shown(rows=8)) == 8


def test_a_narrow_short_screen_still_says_stale():
    lines = texts(shown(sample(written_at=at(45), stale_since=at(1)), cols=40, rows=6))
    assert lines[0].startswith(" - captain stale 45m")
    assert lines[1].startswith(" - stale:")


def test_a_short_screen_scrolls_to_keep_the_selection_in_view():
    lines = shown(rows=12, keys=True, selected="story:ACME-13")
    assert any(line.selected and "ACME-13" in line.text for line in lines)
    assert len(lines) == 12
    assert texts(lines)[-1].startswith(" q quit")


def test_rows_cut_off_below_are_counted():
    lines = texts(shown(rows=12, keys=True))
    assert lines[-3] == "   +4 below"
    assert not any("below" in text for text in texts(shown(rows=40, keys=True)))


def test_the_footer_shows_only_keys_that_act():
    assert texts(shown(rows=30, keys=True))[-1] == " q quit  r refresh  j/k move  enter details"
    empty = board.build(None, fleet.Fleet([]), NOW, TODAY, G)
    assert texts(frame.render(empty, G, 100, 30, keys=True))[-1] == " q quit  r refresh"


def test_asking_puts_the_question_in_the_footer():
    found = board.build(sample(), fleet.Fleet([CAPTAIN]), NOW, TODAY, G)
    lines = texts(frame.render(found, G, 100, 30, keys=True, asking=True))
    assert lines[-1] == f" {frame.ASK}"


def test_the_banner_wraps_on_a_narrow_screen():
    lines = texts(shown(sample(written_at=at(5), stale_since=at(1)), cols=50))
    assert lines[1].startswith(" - stale:")
    assert lines[2].startswith("   ") and lines[2].endswith(" ago")


def test_styled_text_colors_only_when_asked():
    lines = shown()
    assert "\x1b[" not in frame.text(lines, color=False)
    assert "\x1b[1;31m!\x1b[0m" in frame.text(lines, color=True)


def test_yellow_is_only_for_warnings_and_queued_is_plain():
    painted = frame.text(shown(sessions=[CAPTAIN]), color=True)
    assert "\x1b[33m" not in painted and "\x1b[1;33m" not in painted
    queued = next(line for line in shown(sessions=[CAPTAIN]) if "ACME-19" in line.text)
    assert {style for text, style in queued.spans if text.strip()} <= {"", "bold", "dim"}
    stale = frame.text(shown(sample(written_at=at(5), stale_since=at(1))), color=True)
    assert "\x1b[2m-\x1b[0m \x1b[1;33mstale:" in stale


def test_at_sixty_columns_a_long_ref_and_state_keep_the_age():
    for ref, kind in (("SBX-140000000", "a" * 28), ("SBX-1400000000", "a" * 27), ("SBX-1400000000", "a" * 28)):
        status = sample(items=[item("k1", kind, ref, "Print every greeting", 14, "next")], stories=[], hands=[])
        row = section(shown(status, sessions=[], cols=60), "needs you")[0]
        assert row.split()[-2:] == [kind, "14m"] and frame.cells(row) <= 60
