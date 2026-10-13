"""Two screens that polled the server after the user had left them.

The Desk is a single-page app: a Desk Page's wrapper is never removed from the document, and
a Custom HTML Block's DOM stays (hidden) or is detached when the user leaves the workspace. So

* ``maintenance_day_board.js`` cleared its 60 s timer in ``$(wrapper).on('remove')``, which never
  fires, and polled all day. It now stops its own timer once the route is not the board's, and
  ``on_page_show`` restarts it with an immediate refresh. Its cards used ``background:#fff`` with
  an inherited text colour, which is unreadable in dark mode; they take the theme variables.
* ``task_dashboard.js`` refreshed on a 5 min interval and on a realtime event whenever the tab was
  visible, whatever was on screen. Both now go through ``refresh_if_visible``, which asks
  ``onScreen`` first and skips silently (owing one refresh on return) when the block is not shown.
* ``finance_whos_working.js`` ran ``setInterval(load, REFRESH_MS)`` unconditionally. Its heartbeat
  ``tick`` now asks ``onScreen`` first, marks the block stale off screen and loads on return.
* Both hard-coded ``/app/...`` hrefs, which on v16 redirect to ``/desk`` and reload the page;
  they use ``frappe.utils.get_form_link``.

Bench-free: the scripts are read as text, comments stripped.

Run: python -m unittest erpnext_enhancements.tests.test_page_polling_lifecycle
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DAY_BOARD = APP / "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js"
TASK_DASHBOARD = APP / "custom_html_blocks/task_dashboard.js"
WHOS_WORKING = APP / "custom_html_blocks/finance_whos_working.js"


def _code(path):
	"""The script with comments stripped: the comments explain every rule below in prose, and
	an absence assertion must not be satisfied (or broken) by its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


class TestDayBoardPolling(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = _code(DAY_BOARD)

	def test_timer_is_not_cleared_by_a_remove_handler(self):
		# Desk pages are never removed, so a 'remove' handler is a timer that never stops.
		self.assertNotRegex(self.code, r"""on\(\s*['"]remove['"]""")

	def test_poll_stops_itself_off_route_and_restarts_on_page_show(self):
		self.assertIn("frappe.pages['maintenance-day-board'].on_page_show", self.code)
		self.assertIn("frappe.get_route()[0] === 'maintenance-day-board'", self.code)
		tick = self.code[self.code.index("setInterval(") :]
		self.assertLess(tick.index("isCurrent()"), tick.index("refresh()"), "the route check must come first")

	def test_cards_do_not_hard_code_a_white_background(self):
		self.assertNotRegex(self.code, r"background:\s*#fff\b")
		self.assertIn("var(--card-bg", self.code)
		self.assertIn("var(--text-color)", self.code)


class TestTaskDashboardPolling(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = _code(TASK_DASHBOARD)

	def test_interval_and_realtime_go_through_the_on_screen_guard(self):
		self.assertIn("function onScreen(", self.code)
		guard = self.code[self.code.index("function refresh_if_visible(") :]
		guard = guard[: guard.index("\n        }\n")]
		self.assertIn("onScreen(container)", guard)
		self.assertRegex(self.code, r"setInterval\(\s*refresh_if_visible\b")
		self.assertNotRegex(self.code, r"setInterval\(\s*refresh\b")
		realtime = self.code[self.code.index("frappe.utils.debounce(") :]
		self.assertIn("refresh_if_visible()", realtime[: realtime.index("DEBOUNCE_MS")])

	def test_a_failed_first_load_says_so_without_wiping_good_data(self):
		self.assertIn("container.__ee_rendered", self.code)
		self.assertIn("Could not load the dashboard", self.code)


class TestWhosWorkingPolling(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = _code(WHOS_WORKING)

	def test_the_timer_runs_a_guarded_heartbeat_not_load(self):
		self.assertNotRegex(self.code, r"setInterval\(\s*load\b")
		self.assertRegex(self.code, r"setInterval\(\s*tick\b")
		tick = self.code[self.code.index("function tick(") :]
		tick = tick[: tick.index("\n        }\n")]
		self.assertIn("onScreen(container)", tick)
		self.assertIn("state.stale", tick)

	def test_a_block_registers_no_router_handler_of_its_own(self):
		# workspace_block_return.js owns the one router handler (test_workspace_block_return).
		self.assertNotIn("frappe.router.on(", self.code)


class TestNoHardCodedAppLinks(unittest.TestCase):
	def test_neither_script_links_to_slash_app(self):
		for path in (DAY_BOARD, TASK_DASHBOARD):
			with self.subTest(script=path.name):
				self.assertNotRegex(_code(path), r"""['"`(]/app/""")


if __name__ == "__main__":
	unittest.main()
