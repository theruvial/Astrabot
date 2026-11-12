"""Tests for the portfolio page layout (cogs/portfolio_render.py): the hero,
project cards, skill chips and timeline built from the CV, and that the
layout can never let CV text inject HTML or take the page down.

Run from the repo root:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import portfolio_render as pr

CV = """# Thea Arcadia

**Junior Software Developer** · Tampa Bay, FL · Open to remote

me@example.com · [linkedin.com/in/someone](https://www.linkedin.com/in/someone/) · [theruvial.com](https://theruvial.com)

## Summary

Self-taught developer. Second line of the summary.

## Technical Skills

- **Languages:** Python (strongest), SQL (CTEs, window functions), bash
- **Tools:** Git, AI coding assistants

## Experience

**Associate** — Amazon · *Oct 2025 – Present*

- Filed safety reports.

## Projects

### Hand-written work (2019–2025)

**Astrabot** — Discord bot · Python, discord.py · *2019 – present* · [github.com/theruvial/Astrabot](https://github.com/theruvial/Astrabot)

- Built it.

### Self-designed, built with AI coding tools (2026)

**AstraThought** — task classifier · Python, SetFit, AI-assisted · [repo](https://github.com/theruvial/AstraThought)

- Trained a model.

### Homelab

- Proxmox host on ZFS.

## Education

**GED** — Florida · *2023*\\
**Self-taught** · *2019 – present*
"""


class HeroTests(unittest.TestCase):
    def setUp(self):
        self.page = pr.render_portfolio(CV)

    def test_name_role_and_place_are_in_the_hero(self):
        self.assertIn("<h1>Thea Arcadia</h1>", self.page)
        self.assertIn('<p class="role">Junior Software Developer</p>', self.page)
        self.assertIn('<span class="chip">Tampa Bay, FL</span>', self.page)

    def test_the_summary_becomes_the_intro_not_a_section(self):
        self.assertIn('<p class="lede">Self-taught developer. Second line of the summary.</p>', self.page)
        self.assertNotIn('id="summary"', self.page)

    def test_contact_lines_become_buttons_and_the_email_comes_first(self):
        self.assertIn('<a class="btn primary" href="mailto:me@example.com">me@example.com</a>', self.page)
        self.assertIn('<a class="btn" href="https://www.linkedin.com/in/someone/"', self.page)
        self.assertLess(self.page.index("mailto:"), self.page.index("linkedin"))

    def test_a_link_to_this_very_site_is_left_out(self):
        self.assertNotIn("https://theruvial.com", self.page)

    def test_initials_for_the_monogram(self):
        self.assertIn('class="mono" aria-hidden="true">TA<', self.page)
        self.assertEqual(pr._initials("Thea <sub>(she/her)</sub> Arcadia"), "TA")


class SectionTests(unittest.TestCase):
    def setUp(self):
        self.page = pr.render_portfolio(CV)

    def test_projects_come_first_whatever_order_the_cv_uses(self):
        order = [self.page.index(f'id="{s}"') for s in ("projects", "technical-skills", "experience", "education")]
        self.assertEqual(order, sorted(order))
        nav = self.page[: self.page.index("</nav>")]
        self.assertIn('<a href="#technical-skills">Skills</a>', nav)
        self.assertLess(nav.index("#projects"), nav.index("#experience"))

    def test_a_project_becomes_a_card_with_chips_dates_and_a_link_button(self):
        self.assertIn("<h4>Astrabot</h4>", self.page)
        self.assertIn('<p class="sub">Discord bot</p>', self.page)
        self.assertIn('<span class="chip">discord.py</span>', self.page)
        self.assertIn('<span class="when"><em>2019 – present</em></span>', self.page)
        self.assertIn('<a class="btn" href="https://github.com/theruvial/Astrabot"', self.page)
        self.assertIn("<li>Built it.</li>", self.page)

    def test_projects_stay_in_the_cvs_order_and_ai_work_is_marked(self):
        hand = self.page.index("Hand-written work")
        ai = self.page.index("Self-designed, built with AI")
        self.assertLess(hand, ai)
        self.assertIn('<div class="group ai">', self.page)
        self.assertIn('<span class="chip ai">AI-assisted</span>', self.page)
        self.assertEqual(self.page.count('<div class="group">'), 2)   # hand-written + homelab

    def test_a_group_of_plain_bullets_still_gets_a_card(self):
        self.assertIn("<li>Proxmox host on ZFS.</li>", self.page)

    def test_skills_are_chips_and_commas_inside_brackets_do_not_split(self):
        self.assertIn("<h4>Languages</h4>", self.page)
        self.assertIn('<span class="chip">SQL (CTEs, window functions)</span>', self.page)
        self.assertIn('<span class="chip ai">AI coding assistants</span>', self.page)

    def test_experience_and_education_are_timeline_steps(self):
        self.assertIn('<div class="step-head"><h4>Associate</h4><span class="when"><em>Oct 2025 – Present</em></span></div><p class="meta">Amazon</p>', self.page)
        self.assertIn('class="closing wrap reveal"', self.page)
        self.assertIn("Get in touch", self.page)
        self.assertEqual(self.page.count('class="step reveal"'), 3)   # one job, two education lines
        self.assertIn("<h4>Self-taught</h4>", self.page)


class SafetyTests(unittest.TestCase):
    def test_html_in_the_cv_is_escaped_everywhere(self):
        evil = CV.replace("Built it.", "<script>alert(1)</script> [x](javascript:alert(1))") \
                 .replace("Python (strongest)", "<img src=x onerror=y>") \
                 .replace("Amazon", "<b>Amazon</b>")
        page = pr.render_portfolio(evil)
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("javascript:", page)
        self.assertNotIn("<b>Amazon", page)
        self.assertIn("&lt;script&gt;", page)

    def test_a_bad_email_is_not_made_into_a_mailto_link(self):
        page = pr.render_portfolio(CV.replace("me@example.com", 'x"onclick="y@a.co'))
        self.assertNotIn("mailto:x", page)

    def test_an_empty_or_odd_cv_never_raises(self):
        for text in ("", "just words", "## Projects\n\n### Only a group", "# Name\n\n## Experience\n- x"):
            self.assertIsInstance(pr.render_portfolio(text), str)

    def test_a_layout_bug_falls_back_to_the_plain_layout(self):
        original = pr._render
        pr._render = lambda _md: 1 / 0
        try:
            page = pr.render_portfolio(CV)
        finally:
            pr._render = original
        self.assertIn('<main class="wrap plain">', page)
        self.assertIn("<h1>Thea Arcadia</h1>", page)

    def test_unknown_sections_are_kept_after_the_known_ones(self):
        page = pr.render_portfolio(CV + "\n## Volunteering\n\n**Food bank** — Helper · *2024*\n")
        self.assertIn('id="volunteering"', page)
        self.assertGreater(page.index('id="volunteering"'), page.index('id="education"'))


if __name__ == "__main__":
    unittest.main()
