"""Tests for the CV to HTML renderer. Pure functions, no network or bot.

Run from the repo root:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import portfolio_cv as cv

SAMPLE = """# Thea Arcadia

**Junior Software Developer** · Open to remote

## Summary

Self-taught developer.\\
Strongest in Python.

## Experience

**Role** — Place · *2024 – Present*

- Did a thing with [a link](https://example.com/x?a=1&b=2).
- Did another thing.

### Sub heading

- Nested section bullet.

## Education

GED
"""


class InlineTests(unittest.TestCase):
    def test_html_in_the_cv_is_escaped(self):
        self.assertEqual(cv.inline("<script>alert(1)</script>"),
                         "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_bold_italic_and_links(self):
        out = cv.inline("**b** and *i* and [x](https://e.com)")
        self.assertIn("<strong>b</strong>", out)
        self.assertIn("<em>i</em>", out)
        self.assertIn('<a href="https://e.com" rel="noopener">x</a>', out)

    def test_unsafe_links_keep_the_words_but_lose_the_link(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "//evil.example/x"):
            out = cv.inline(f"[click me]({bad})")
            self.assertEqual(out, "click me")

    def test_mailto_is_allowed(self):
        self.assertIn('href="mailto:a@b.co"', cv.inline("[mail](mailto:a@b.co)"))

    def test_quotes_in_urls_cannot_break_out_of_the_attribute(self):
        out = cv.inline('[x](https://e.com/"onmouseover="alert(1))')
        self.assertNotIn('"onmouseover', out.replace("&quot;", ""))
        self.assertNotIn("<script", out)

    def test_a_sub_pair_is_allowed_for_the_pronouns_line(self):
        self.assertEqual(cv.inline("Thea Arcadia <sub>(she/her)</sub>"),
                         "Thea Arcadia <sub>(she/her)</sub>")
        self.assertEqual(cv.inline("x<sup>2</sup>"), "x<sup>2</sup>")

    def test_no_other_html_and_no_loose_sub_tags_get_through(self):
        for text in ("<sub>never closed", "<sub onclick=x>a</sub>", "<SUB>a</SUB>",
                     "<sub><script>x</script></sub>", "</sub>stray<sub>"):
            out = cv.inline(text)
            self.assertNotIn("<script", out)
            self.assertNotIn("onclick=x>", out.replace("&lt;sub onclick=x&gt;", ""))
        self.assertEqual(cv.inline("<sub>never closed"), "&lt;sub&gt;never closed")
        self.assertIn("&lt;sub onclick=x&gt;", cv.inline("<sub onclick=x>a</sub>"))
        self.assertIn("&lt;script&gt;", cv.inline("<sub><script>x</script></sub>"))

    def test_asterisks_inside_words_are_left_alone(self):
        self.assertEqual(cv.inline("2*3*4"), "2*3*4")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.page = cv.render_cv(SAMPLE)

    def test_one_section_per_h2(self):
        self.assertEqual(self.page.count("<section>"), 3)
        self.assertEqual(self.page.count("</section>"), 3)

    def test_name_headline_and_headings(self):
        self.assertIn("<h1>Thea Arcadia</h1>", self.page)
        self.assertIn("<h2>Experience</h2>", self.page)
        self.assertIn("<h3>Sub heading</h3>", self.page)

    def test_bullets_group_into_lists(self):
        self.assertEqual(self.page.count("<ul>"), 2)
        self.assertEqual(self.page.count("<li>"), 3)

    def test_trailing_backslash_is_a_line_break(self):
        self.assertIn("Self-taught developer.<br>Strongest in Python.", self.page)

    def test_an_empty_cv_renders_nothing(self):
        self.assertEqual(cv.render_cv("  \n\n"), "")


class TemplateTests(unittest.TestCase):
    def test_pronouns_under_the_name_are_styled_as_a_line_of_their_own(self):
        template = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "cogs", "portfolio_page.html"), encoding="utf-8").read()
        self.assertIn("h1 sub { display: block;", template)


class StructureTests(unittest.TestCase):
    def test_section_titles_and_missing_sections(self):
        self.assertEqual(cv.section_titles(SAMPLE), ["Summary", "Experience", "Education"])
        self.assertEqual(cv.missing_sections(SAMPLE), ["Technical Skills", "Projects"])
        self.assertEqual(cv.missing_sections("## summary\n## technical skills\n## experience\n"
                                             "## projects\n## education\n"), [])


class SummarizeChangeTests(unittest.TestCase):
    def test_no_change_is_empty(self):
        self.assertEqual(cv.summarize_change(SAMPLE, SAMPLE), [])

    def test_added_and_removed_lines_show_with_signs(self):
        out = cv.summarize_change("- a\n- b", "- a\n- c")
        self.assertIn("-- b", out)
        self.assertIn("+- c", out)

    def test_long_lines_and_long_diffs_are_cut(self):
        out = cv.summarize_change("", "\n".join(f"line {i} " + "x" * 200 for i in range(30)),
                                  limit=5)
        self.assertEqual(len(out), 6)
        self.assertTrue(out[-1].startswith("... and 25 more"))
        self.assertTrue(all(len(line) <= 140 for line in out[:5]))


if __name__ == "__main__":
    unittest.main()
