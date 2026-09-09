import json
import unittest
from pathlib import Path

from deckhand import sections

VALID = (Path(__file__).parent / "fixtures" / "body-valid.md").read_text(encoding="utf-8")
INVALID = (Path(__file__).parent / "fixtures" / "body-invalid.md").read_text(encoding="utf-8")
ISSUE = json.loads((Path(__file__).parent / "fixtures" / "issue.json").read_text(encoding="utf-8"))


class ParseTests(unittest.TestCase):
    def test_lists_every_section_in_order(self):
        _, parsed = sections.parse(VALID)
        self.assertEqual(
            [name for name, _ in parsed],
            ["Story", "Scope", "Acceptance Criteria", "Plan", "Notes"],
        )

    def test_get_returns_body_without_heading(self):
        self.assertIn("### Task 1: Store method", sections.get(VALID, "Plan"))

    def test_get_keeps_foreign_headings_inside_a_section(self):
        text = "### Plan\n\n# Title\n\n### Task 1: A\n\nbody\n\n### Notes\n\n- n\n"
        self.assertEqual(sections.get(text, "Plan"), "# Title\n\n### Task 1: A\n\nbody")
        self.assertEqual(sections.get(text, "Notes"), "- n")

    def test_get_missing_raises(self):
        with self.assertRaises(KeyError):
            sections.get("### Story\n\nx\n", "Plan")

    def test_get_missing_answers_the_default_when_one_is_given(self):
        self.assertEqual(sections.get("### Story\n\nx\n", "Plan", ""), "")
        self.assertIsNone(sections.get("### Story\n\nx\n", "Plan", None))
        self.assertEqual(sections.get(VALID, "Notes", ""), "- Grants live in `internal/store/grant.go`")

    def test_replace_swaps_the_content(self):
        out = sections.replace(VALID, "Plan", "### Task 1: X\n\n- [ ] step")
        self.assertEqual(sections.get(out, "Plan"), "### Task 1: X\n\n- [ ] step")
        self.assertEqual(sections.get(out, "Notes"), "- Grants live in `internal/store/grant.go`")

    def test_replace_appends_missing_section(self):
        out = sections.replace("### Story\n\nx\n", "Notes", "- new")
        self.assertEqual(sections.get(out, "Notes"), "- new")
        self.assertTrue(out.endswith("\n"))

    def test_render_round_trips(self):
        pre, parsed = sections.parse(VALID)
        again = sections.render(pre, parsed)
        self.assertEqual(sections.parse(again), (pre, parsed))

    def test_replace_rejects_content_with_a_section_heading(self):
        with self.assertRaises(ValueError):
            sections.replace(VALID, "Notes", "before\n\n### Plan\n\nafter")

    def test_replace_touches_only_the_first_duplicate(self):
        text = "### Story\n\nx\n\n### Notes\n\n- one\n\n### Notes\n\n- two\n"
        out = sections.replace(text, "Notes", "- new")
        _, parsed = sections.parse(out)
        self.assertEqual(
            [body for name, body in parsed if name == "Notes"],
            ["- new", "- two"],
        )

    def test_replace_inserts_missing_section_at_its_canonical_position(self):
        out = sections.replace(INVALID, "Plan", "### Task 1: X")
        _, parsed = sections.parse(out)
        self.assertEqual(
            [name for name, _ in parsed],
            ["Story", "Scope", "Acceptance Criteria", "Plan", "Notes"],
        )

    def test_crlf_parses_the_same_as_lf(self):
        crlf = VALID.replace("\n", "\r\n")
        self.assertEqual(sections.parse(crlf), sections.parse(VALID))

    def test_line_separator_survives_a_replace_round_trip(self):
        text = "### Story\n\nx\n"
        content = "line one line two"
        out = sections.replace(text, "Notes", content)
        self.assertEqual(sections.get(out, "Notes"), content)

    def test_replace_normalizes_crlf_content(self):
        text = "### Story\n\nx\n"
        out = sections.replace(text, "Notes", "line one\r\nline two\r\n")
        self.assertNotIn("\r", out)
        self.assertEqual(sections.get(out, "Notes"), "line one\nline two")

    def test_issue_body_parses_to_all_sections_in_order(self):
        body = ISSUE["body"]
        preamble, parsed = sections.parse(body)
        self.assertEqual(
            [name for name, _ in parsed],
            ["Story", "Scope", "Acceptance Criteria", "Plan", "Notes"],
        )
        plan = sections.get(body, "Plan")
        self.assertIn("### Task 1", plan)
        self.assertIn("### Task 2", plan)
        self.assertIn("# Guest Collections Implementation Plan", plan)
        rendered = sections.render(preamble, parsed)
        self.assertEqual(rendered.count("<details>"), 1)
        self.assertEqual(sections.parse(rendered), (preamble, parsed))

    def test_render_folds_the_plan_and_only_the_plan(self):
        story = ("Story", "As a x, I want y, so that z.")
        body = sections.render("", [story, ("Plan", "### Task 1\n\n- [ ] step")])
        folded = "### Plan\n\n<details>\n<summary>Show the plan</summary>\n\n### Task 1\n\n- [ ] step\n\n</details>\n"
        self.assertIn(folded, body)
        self.assertEqual(body.count("<details>"), 1)

    def test_get_returns_the_plan_without_its_fold(self):
        body = sections.render("", [("Plan", "### Task 1\n\n- [ ] step")])
        self.assertEqual(sections.get(body, "Plan"), "### Task 1\n\n- [ ] step")

    def test_render_does_not_fold_a_plan_that_is_already_folded(self):
        once = sections.render("", [("Plan", "### Task 1")])
        twice = sections.render("", sections.parse(once)[1])
        self.assertEqual(once, twice)

    def test_replace_keeps_the_fold_when_another_section_changes(self):
        body = sections.render("", [("Plan", "### Task 1"), ("Notes", "a")])
        changed = sections.replace(body, "Notes", "b")
        self.assertEqual(sections.get(changed, "Plan"), "### Task 1")
        self.assertEqual(changed.count("<details>"), 1)

    def test_an_empty_plan_is_not_folded(self):
        self.assertNotIn("<details>", sections.render("", [("Plan", "")]))

    def test_a_fold_the_plan_comes_back_with_is_unwrapped(self):
        for wrapper in (
            "<details open>\n<summary>Plan</summary>\n\nPLAN\n\n</details>",
            "<details>\n<summary> Plan </summary>\n\nPLAN\n\n</details>",
            "<details>\n\n<summary>Plan</summary>\n\nPLAN\n\n</details>",
            "<details>\n<summary><b>Plan</b></summary>\n\nPLAN\n\n</details>",
        ):
            with self.subTest(wrapper=wrapper[:40]):
                body = f"### Plan\n\n{wrapper.replace('PLAN', '### Task 1\n\n- [ ] step')}\n"
                self.assertEqual(sections.get(body, "Plan"), "### Task 1\n\n- [ ] step")

    def test_a_plan_that_opens_with_a_fold_of_its_own_is_left_alone(self):
        plan = (
            "<details>\n<summary>Context</summary>\n\nbackground\n\n</details>\n\n"
            "### Task 1\n\n<details>\n<summary>Detail</summary>\n\nmore\n\n</details>"
        )
        self.assertEqual(sections.get(f"### Plan\n\n{plan}\n", "Plan"), plan)

    def test_bare_renders_the_body_the_model_edits_without_the_fold(self):
        body = sections.render("", [("Plan", "### Task 1"), ("Notes", "a")])
        bare = sections.bare(body)
        self.assertNotIn("<details>", bare)
        self.assertEqual(sections.parse(bare), sections.parse(body))


if __name__ == "__main__":
    unittest.main()
