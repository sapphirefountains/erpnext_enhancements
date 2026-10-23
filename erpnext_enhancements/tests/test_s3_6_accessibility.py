"""Custom pages S3.6 (v1.656.0): the accessibility pass, pinned file by file.

Each check names the file and the element it fixed, so a regression points at one line rather
than at a repo-wide scan that is noisy enough to be ignored:

* **Keyboard.** A clickable element is a real ``<button type="button">``, or carries
  ``role="button"``, ``tabindex="0"`` and an Enter/Space keydown that calls the click's handler.
  The Rental Planner's booking bars and finished wizard steps and Plan a Trip's toggle chips are
  buttons; the planners' day numbers and crew-view date heads keep their markup and gain the role,
  the tab stop and the key.
* **Names.** A button that is only a glyph (‹ › ↻ × or an emoji) has an ``aria-label``.
* **Colour alone.** The KPI Cockpit's grade and the Task Dashboard's priority were only a border
  colour; each now carries its word. Three progress bars said their percent only in a tooltip;
  each is now a ``progressbar`` with ``aria-valuenow``.
* **Translation.** The GA4 dashboard's visible strings go through ``__()``.

``scripts/test_rental_planner_history.js`` drives the Rental Planner's bars and steps with the
keyboard (run from ``test_rental_planner``). Bench-free: reads the sources, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_s3_6_accessibility -v
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

RENTAL = APP / "asset_management/page/rental_planner/rental_planner.js"
TRIP = APP / "travel_management/page/plan_a_trip/plan_a_trip.js"
PROJECT = APP / "project_enhancements/page/project_planner/project_planner.js"
MAINTENANCE = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
SCANNER = APP / "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js"
CONSOLE = APP / "device_management/page/device_console/device_console.js"
INSPECTION = APP / "quality/page/inspection_wizard/inspection_wizard.js"
GA4 = APP / "integration_hub/page/ga4_dashboard/ga4_dashboard.js"
BLOCKS = APP / "custom_html_blocks"


def _code(path):
	"""The script with comments stripped: an absence assertion must not trip on its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _statement(code, start):
	"""From ``start`` to the end of the jQuery chain it begins: the first ``;`` outside every
	bracket and string, so a handler's body inside the chain does not end it early."""
	depth = 0
	quote = None
	for at in range(start, len(code)):
		char = code[at]
		if quote:
			if char == quote and code[at - 1] != chr(92):
				quote = None
		elif char in "'\"`":
			quote = char
		elif char in "([{":
			depth += 1
		elif char in ")]}":
			depth -= 1
		elif char == ";" and depth == 0:
			return code[start : at + 1]
	raise AssertionError(f"no end to the statement at {code[start : start + 80]!r}")


def _rental_css():
	return re.sub(r"/\*[\s\S]*?\*/", "", RENTAL.with_suffix(".css").read_text(encoding="utf-8"))


GLYPH_BUTTON = re.compile(r"<button\b[^>]*>\s*(?:[‹›↻×✕🔍📷]|&#\d+;|&times;)\s*</button>")


class TestTheRentalPlannerAnswersTheKeyboard(unittest.TestCase):
	def test_a_booking_bar_is_a_button(self):
		code = _code(RENTAL)
		self.assertIn('const $bar = $(\'<button type="button" class="rp-bar"></button>\')', code)
		self.assertNotIn('<div class="rp-bar"', code)

	def test_a_booking_bar_is_named(self):
		"""The prep and cleaning bars draw no text: without the label a screen reader says "button"."""
		self.assertIn(
			'$bar.attr({ title: facts.join("\\n"), "aria-label": facts.join(", ") });', _code(RENTAL)
		)

	def test_a_finished_step_is_a_button_and_only_it_is_clickable(self):
		code = _code(RENTAL)
		self.assertIn("const done = i !== current && i <= reachable;", code)
		self.assertIn(
			'$(done ? \'<button type="button" class="rp-step"></button>\' : \'<div class="rp-step"></div>\')',
			code,
		)
		self.assertIn('$s.addClass("rp-done").on("click", (event) => {', code)
		self.assertIn('$s.addClass("rp-active").attr("aria-current", "step")', code)

	def test_a_step_reached_from_the_keyboard_focuses_its_heading(self):
		"""A keyboard click (detail 0) moves focus to the new step's heading; a mouse click leaves
		it. The harness drives both."""
		code = _code(RENTAL)
		self.assertIn("if (event && event.detail === 0) this.focus_heading_until = Date.now() + 2000;", code)
		show = code[code.index("show_step(step) {") :]
		show = show[: show.index("\n\t}\n")]
		self.assertIn('this.$card.find("h4").first().attr("tabindex", "-1")[0]', show)
		self.assertIn("heading.focus();", show)

	def test_a_bar_label_truncates_on_an_inner_span(self):
		"""Some engines ignore text-overflow on a <button>, so the ellipsis is on a span inside it."""
		code = _code(RENTAL)
		self.assertIn("$('<span class=\"rp-bar-text\"></span>').text(text).appendTo($bar)", code)
		# The page's CSS moved out of the script into rental_planner.css in S3.4f (v1.670.0).
		self.assertRegex(
			_rental_css(),
			r"(?m)^\.rp-bar-text \{\s*display: block;\s*overflow: hidden;\s*white-space: nowrap;"
			r"\s*text-overflow: ellipsis;\s*\}",
		)

	def test_focus_is_visible(self):
		css = _rental_css()
		for selector in (".rp-bar:focus-visible", ".rp-step:focus-visible"):
			with self.subTest(selector=selector):
				self.assertRegex(
					css, r"(?m)^" + re.escape(selector) + r" \{\s*outline: 2px solid var\(--sf-focus\);"
				)

	def test_the_board_nav_buttons_are_labelled(self):
		code = _code(RENTAL)
		for glyph, label in (("‹", "Earlier"), ("›", "Later")):
			self.assertIn(
				f'.text("{glyph}").attr({{ title: __("{label}"), "aria-label": __("{label}") }})', code
			)


class TestPlanATripChipsAreToggleButtons(unittest.TestCase):
	"""Who is on a booking, who stays free, and who the comparison shows."""

	def test_the_three_toggle_chips_are_buttons(self):
		code = _code(TRIP)
		toggles = re.findall(
			r'<button type="button" class="tp-chip \$\{(\w+) \? "tp-on" : ""\}" aria-pressed="\$\{\1\}" data-chip=',
			code,
		)
		self.assertEqual(sorted(toggles), ["checked", "on", "on"])

	def test_a_redraw_keeps_focus_on_the_chip(self):
		"""Each chip has a stable data-chip key and redraws through render_keeping_focus, which
		puts focus back on the chip drawn in its place (scripts/test_wizard_back_forward.mjs
		drives it)."""
		code = _code(TRIP)
		for key in (
			"`member:${card.group}:${t.employee}`",
			"`guest:${card.group}:${member.traveler}`",
			"`shown:${person.employee}`",
		):
			self.assertIn(key, code)
		self.assertEqual(code.count("this.render_keeping_focus();"), 3)
		keeper = code[code.index("render_keeping_focus() {") :]
		keeper = keeper[: keeper.index("\n\t}\n")]
		self.assertIn('active.getAttribute("data-chip")', keeper)
		self.assertIn("if (chip) chip.focus();", keeper)

	def test_a_chip_on_a_protected_booking_is_disabled(self):
		"""On an Expense Claim the booking cannot change; its chips must not announce as toggles."""
		self.assertEqual(_code(TRIP).count('${card.protected ? " disabled" : ""}'), 2)

	def test_no_chip_with_a_click_is_a_span(self):
		code = _code(TRIP)
		for chip in re.finditer(r'<span class="tp-chip ', code):
			chain = _statement(code, code.rindex("$(", 0, chip.start()))
			self.assertNotIn('.on("click"', chain, chain[:160])

	def test_focus_is_visible(self):
		"""The page's CSS moved out of the script into plan_a_trip.css in S3.4c (v1.661.0)."""
		css = re.sub(r"/\*[\s\S]*?\*/", "", TRIP.with_suffix(".css").read_text(encoding="utf-8"))
		rule = re.search(r"(?m)^\.tp-chip:focus-visible\s*\{([^}]*)\}", css)
		self.assertIsNotNone(rule, "no .tp-chip:focus-visible rule in plan_a_trip.css")
		self.assertRegex(rule.group(1), r"outline:\s*2px solid var\(--sf-focus\);")


class TestPlannerNavButtonsAreLabelled(unittest.TestCase):
	"""‹ › ↻ in the toolbars, the route view's day stepper, My week's week stepper and Today's day stepper."""

	EXPECTED = {
		PROJECT: ["Earlier", "Later", "Refresh", "Previous day", "Next day", "Previous week", "Next week", "Previous day", "Next day"],
		MAINTENANCE: ["Earlier", "Later", "Refresh"],
	}

	def test_every_glyph_nav_button_has_an_aria_label(self):
		for path, labels in self.EXPECTED.items():
			code = _code(path)
			found = []
			for button in re.finditer(r"\$\('<button[^>]*>[‹›↻]</button>'\)", code):
				chain = _statement(code, button.start())
				label = re.search(r'\.attr\("aria-label", __\("([^"]+)"\)\)', chain)
				self.assertIsNotNone(label, f"{path.name}: {chain[:120]}")
				found.append(label.group(1))
			self.assertEqual(found, labels, path.name)


class TestPlannerDayLinksAnswerTheKeyboard(unittest.TestCase):
	"""A day number or a crew-view date head opens its week; Enter and Space do the same."""

	def test_project_planner(self):
		code = _code(PROJECT)
		self.assertIn('<span class="pp-day-num" role="button" tabindex="0" data-week=', code)
		self.assertIn(
			'class="pp-crew-head${ymd === today ? " pp-today" : ""}" role="button" tabindex="0" data-week=',
			code,
		)
		binder = code[code.index("bind_week_links($root) {") :]
		binder = binder[: binder.index("\n\t}\n")]
		self.assertIn('.on("click", open)', binder)
		self.assertIn('if (e.key !== "Enter" && e.key !== " ") return;', binder)
		self.assertIn("open(e);", binder)
		self.assertEqual(code.count("this.bind_week_links($"), 2)
		self.assertNotIn('find("[data-week]").on("click", (e) =>', code)
		# The page's CSS moved out of the script into project_planner.css in S3.4d (v1.664.0).
		css = re.sub(r"/\*[\s\S]*?\*/", "", PROJECT.with_suffix(".css").read_text(encoding="utf-8"))
		self.assertRegex(
			css,
			r"(?m)^\.pp-day-num:focus-visible,\s*\.pp-crew-head:focus-visible,\s*\.pp-title:focus-visible\s*"
			r"\{\s*outline: 2px solid var\(--sf-focus\);",
		)

	def test_maintenance_planner(self):
		code = _code(MAINTENANCE)
		day = _statement(code, code.index('$(\'<span class="mp-day-num"'))
		self.assertIn('role="button" tabindex="0"', day)
		self.assertIn('.on("click", () => this.go("week", ymd))', day)
		self.assertIn('.on("keydown", (e) => {', day)
		self.assertIn('this.go("week", ymd);', day[day.index("keydown") :])
		self.assertIn(
			'class="mp-crew-head${ymd === today ? " mp-today" : ""}" role="button" tabindex="0" data-week=',
			code,
		)
		crew = _statement(code, code.index('.find("[data-week]")'))
		self.assertIn('.on("click", open_week)', crew)
		self.assertIn("open_week(e);", crew)
		# The page's CSS moved out of the script into maintenance_planner.css in S3.4e (v1.673.0).
		css = re.sub(r"/\*[\s\S]*?\*/", "", MAINTENANCE.with_suffix(".css").read_text(encoding="utf-8"))
		self.assertRegex(
			css,
			r"(?m)^\.mp-day-num:focus-visible,\s*\.mp-crew-head:focus-visible,\s*\.mp-title:focus-visible\s*"
			r"\{\s*outline: 2px solid var\(--sf-focus\);",
		)


class TestPlannerDayLinksNameTheirActionAndMoveFocus(unittest.TestCase):
	"""A day link's aria-label says what it does (and changes with the quick looks' "See
	everyone's day"), and a week opened from the keyboard focuses the toolbar title."""

	def check(self, path, prefix, see_everyone, keyboard_paths):
		code = _code(path)
		self.assertEqual(code.count('__("Show the week of {0}", [this.day_words(ymd)])'), 2, path.name)
		self.assertEqual(code.count('__("See everyone\'s day on {0}", ['), see_everyone, path.name)
		self.assertIn(f'this.$title = $(\'<span class="{prefix}-title" tabindex="-1"></span>\')', code)
		self.assertEqual(code.count("this.focus_title_until = Date.now() + 2000;"), keyboard_paths, path.name)
		self.assertIn("this.$title.text(this.title());\n\t\tthis.take_title_focus();", code)
		taker = code[code.index("take_title_focus() {") :]
		self.assertIn("title.focus();", taker[: taker.index("\n\t}\n")])

	def test_project_planner(self):
		self.check(PROJECT, "pp", see_everyone=1, keyboard_paths=1)

	def test_maintenance_planner(self):
		self.check(MAINTENANCE, "mp", see_everyone=2, keyboard_paths=2)


class TestGlyphButtonsAreNamed(unittest.TestCase):
	"""The scanners' camera, find and remove buttons and the inspection photo button."""

	def test_every_glyph_button_has_an_aria_label(self):
		for path, count in ((SCANNER, 3), (CONSOLE, 1), (INSPECTION, 1)):
			buttons = GLYPH_BUTTON.findall(_code(path))
			self.assertEqual(len(buttons), count, path.name)
			for button in buttons:
				self.assertIn('aria-label="${__(', button, f"{path.name}: {button}")


class TestStatusesCarryTheirWord(unittest.TestCase):
	def test_the_kpi_cockpit_grade(self):
		code = _code(BLOCKS / "kpi_cockpit.js")
		self.assertIn(
			'const word = { good: __("Good"), watch: __("Watch"), bad: __("Bad") }[status] || v.status || "";',
			code,
		)
		self.assertIn('<div class="kpi-card-label">${grade}${esc(v.label || v.kpi_key)}</div>', code)
		self.assertIn(".kpi-status {", (BLOCKS / "kpi_cockpit.css").read_text(encoding="utf-8"))

	def test_the_task_dashboard_priority(self):
		code = _code(BLOCKS / "task_dashboard.js")
		for word in ("Urgent", "High", "Medium", "Low"):
			self.assertIn(f'__("{word}")', code)
		self.assertIn(
			'<div class="tdb-task-people">${priority_word(slug)}${assignee_chips(task.assignees)}</div>', code
		)
		self.assertIn(".tdb-prio {", (BLOCKS / "task_dashboard.css").read_text(encoding="utf-8"))

	def test_the_progress_bars_say_their_percent(self):
		for name, prefix in (
			("design_wip_board", "dwb"),
			("production_wip_aging", "pwa"),
			("service_day_board", "odb"),
		):
			code = _code(BLOCKS / f"{name}.js")
			bar = re.search(rf'<span class="{prefix}-bar"[^>]*>', code)
			self.assertIsNotNone(bar, name)
			for attr in (
				'role="progressbar"',
				'aria-valuemin="0"',
				'aria-valuemax="100"',
				'aria-valuenow="${pct}"',
				"aria-label=",
			):
				self.assertIn(attr, bar.group(0), name)


class TestTheGa4DashboardIsTranslated(unittest.TestCase):
	def test_visible_strings_go_through_translation(self):
		code = _code(GA4)
		self.assertIn("title: __('GA4 Dashboard'),", code)
		for tag in ("th", "h4", "p"):
			for text in re.findall(rf"<{tag}\b[^>]*>([^<]*)</{tag}>", code):
				self.assertRegex(text.strip(), r"^\$\{__\(", f"<{tag}>{text}</{tag}>")
		for title in re.findall(r"\btitle: ([^,\n]+),", code):
			self.assertTrue(title.startswith("__("), title)
		self.assertIn('${__("GA4 Error:")}', code)
		self.assertIn('${__("GSC Error:")}', code)
		self.assertNotIn('"Unknown GA4 error"', code.replace('__("Unknown GA4 error")', ""))


if __name__ == "__main__":
	unittest.main()
