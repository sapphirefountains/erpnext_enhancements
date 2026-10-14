"""Custom pages S3.1 (v1.638.0, TASK-2026-02820): seven Desk pages draw their states from sf_kit.

Each of these pages sat on "Loading…" for good, or went blank, when its server call failed:

* Rental Planner — the fleet timeline stayed on "Loading the fleet...".
* Maintenance Day Board — a minimal line from S1.1, and its 60 s timer passed frappe.call's
  ``silent``, which stops only the msgprint of a 417: a wall screen still stacked a "Server Error"
  dialog every minute while the server answered 5xx.
* Stripe Payments dashboard — no loading or error state at all.
* QuickBooks Online dashboard — no error handling, and the sync-log rows went into markup unescaped.
* Water Engineering Wizard — four calls, none with an error path.
* Device Console and Inventory Scanner Audit — the start-up call had no error path.

Now each loads sf_kit once with ``frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"])``,
falls back to a plain stand-in when the kit never arrives (v16's loader resolves on a failed
file), renders the kit's loading panel, and on a failed load renders an error panel whose
"Try again" runs the load again (or the no-access panel on a 403). A background refresh uses the
kit's quiet path, so frappe puts up no dialog for it.

The behaviour is run too: ``scripts/test_rental_planner_history.js`` (a failed board, Try again)
and ``scripts/test_desk_page_history.js`` (a failed start-up on both scanner pages). These are the
static checks. Bench-free: the scripts are read as text, comments stripped.

Run: python -m unittest erpnext_enhancements.tests.test_s3_page_states
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

PAGES = {
	"rental-planner": APP / "asset_management/page/rental_planner/rental_planner.js",
	"maintenance-day-board": APP / "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js",
	"stripe-payments-dashboard": APP / "stripe_payments/page/stripe_payments_dashboard/stripe_payments_dashboard.js",
	"quickbooks-online-dashboard": APP
	/ "quickbooks_online/page/quickbooks_online_dashboard/quickbooks_online_dashboard.js",
	"water-engineering-wizard": APP / "water_engineering/page/water_engineering_wizard/water_engineering_wizard.js",
	"device-console": APP / "device_management/page/device_console/device_console.js",
	"inventory-scanner-audit": APP / "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js",
}

# The call each page makes on the way in, which must report its failure in the page's own panel.
FIRST_LOAD = {
	"rental-planner": "get_timeline",
	"maintenance-day-board": "get_day_board_data",
	"stripe-payments-dashboard": "get_dashboard_status",
	"quickbooks-online-dashboard": "get_dashboard_status",
	"water-engineering-wizard": "check_permission",
	"device-console": "get_console_bootstrap",
	"inventory-scanner-audit": "get_bootstrap",
}

REQUIRE = re.compile(r"""frappe\.require\(\[\s*(['"])sf_kit\.bundle\.js\1,\s*\1sf_kit\.bundle\.css\1\s*\]""")


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: the comments explain every rule below in prose, and
	an absence assertion must not be satisfied (or broken) by its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _call_of(code, method):
	"""The source from the kit call of `method` up to the end of its argument list's line."""
	match = re.search(r"\.call\(\s*[^,]*?" + re.escape(method) + r"[`'\"]\s*,[^\n]*", code)
	return match.group(0) if match else ""


class TestEveryPageLoadsTheKit(unittest.TestCase):
	def test_the_kit_is_required_both_files_together(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				self.assertRegex(code, REQUIRE)
				self.assertEqual(len(REQUIRE.findall(code)), 1, "load the kit once per page")

	def test_never_the_raw_assets_path(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertNotIn("/assets/erpnext_enhancements/js/sf_kit", _code(path))

	def test_a_kit_that_never_arrived_leaves_the_page_working(self):
		"""v16's loader resolves on a failed file, so the callback runs with no window.sf_kit."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				self.assertRegex(code, r"window\.sf_kit && window\.sf_kit\.state \? window\.sf_kit : \w+\(\)")
				# A missing frappe.require (or one that throws) is not a stuck page either.
				require_at = REQUIRE.search(code).start()
				self.assertIn("try {", code[max(0, require_at - 120) : require_at])
				self.assertIn("catch (e) {", code[require_at : require_at + 200])


class TestTheStates(unittest.TestCase):
	def test_loading_is_the_kits_panel(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertRegex(_code(path), r"state\.render\([^,]+,\s*['\"]loading['\"]")

	def test_the_first_load_is_quiet(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				call = _call_of(_code(path), FIRST_LOAD[name])
				self.assertTrue(call, f"no kit call of {FIRST_LOAD[name]}")
				self.assertIn("quiet: true", call)

	def test_a_failed_load_has_an_error_panel_with_try_again(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				errors = re.findall(r"state\.render\([^;]*?['\"]error['\"],\s*\{[\s\S]*?\}\)", code)
				self.assertTrue(
					any("on_action:" in e and "reload()" not in e for e in errors),
					"an error panel whose action runs the load again",
				)

	def test_a_403_is_the_no_access_panel(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				self.assertRegex(code, r"['\"]no-access['\"]")
				self.assertIn("status === 403", code)

	def test_a_lapsed_session_is_not_a_dead_end(self):
		"""The quiet path raises no sign-in dialog of frappe's; the page offers the way back."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertIn("session_expired", _code(path))

	def test_no_page_writes_its_own_loading_line_any_more(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				self.assertNotIn("Loading the fleet...", code)
				self.assertNotRegex(code, r"""html\(['"`]<div[^>]*>['"`]\s*\+\s*__\(['"]Loading""")


class TestRefreshes(unittest.TestCase):
	def test_the_day_boards_timer_refresh_is_quiet(self):
		code = _code(PAGES["maintenance-day-board"])
		tick = code[code.index("setInterval(") :]
		tick = tick[: tick.index("}, 60000);")]
		self.assertIn("refresh()", tick)
		refresh = code[code.index("function refresh() {") : code.index("function stop() {")]
		self.assertIn("{ quiet: true }", refresh)
		# frappe.call is no longer how the board loads; the kit's quiet path is.
		self.assertNotIn("frappe.call({ method: 'erpnext_enhancements.api.maintenance_board", code)
		self.assertNotIn("silent: Boolean(", code)

	def test_the_day_boards_failed_refresh_keeps_the_board(self):
		refresh = _code(PAGES["maintenance-day-board"])
		refresh = refresh[refresh.index("function refresh() {") : refresh.index("function stop() {")]
		self.assertIn("if (!state.loaded) return failed(sf, err);", refresh)
		self.assertIn("$stale.text(", refresh)

	def test_a_failed_refresh_with_data_showing_keeps_it(self):
		for name in ("stripe-payments-dashboard", "device-console"):
			with self.subTest(page=name):
				code = _code(PAGES[name])
				self.assertIn("sf.toast(", code)
		self.assertIn("if (!loaded) return failed(sf, err);", _code(PAGES["stripe-payments-dashboard"]))

	def test_the_device_counts_refresh_is_quiet(self):
		code = _code(PAGES["device-console"])
		block = code[code.index("refreshCounts() {") : code.index("handleScan(raw) {")]
		self.assertIn("{ quiet: true }", block)

	def test_the_qbo_resync_poll_is_quiet(self):
		code = _code(PAGES["quickbooks-online-dashboard"])
		self.assertIn("{ quiet: true }", _call_of(code, "get_sync_log_summary"))

	def test_a_stale_timeline_reply_never_paints(self):
		code = _code(PAGES["rental-planner"])
		block = code[code.index("load_timeline() {") : code.index("timeline_failed(sf, $timeline, err) {")]
		self.assertGreaterEqual(block.count("if (token !== this._timeline_token) return;"), 3)


class TestQuickBooksLogRowsAreEscaped(unittest.TestCase):
	def setUp(self):
		code = _code(PAGES["quickbooks-online-dashboard"])
		self.rows = code[code.index("function renderLogs(root, logs) {") : code.index("function connectQuickBooks()")]

	def test_every_server_value_goes_through_the_kits_escape(self):
		self.assertIn("const esc = sf.esc;", self.rows)
		for field in (
			"name",
			"sync_type",
			"entity_type",
			"status",
			"created_count",
			"linked_count",
			"manual_review_count",
			"conflict_count",
			"ignored_count",
		):
			with self.subTest(field=field):
				self.assertNotRegex(self.rows, r"\$\{log\." + field + r"\b", "a raw interpolation")
				self.assertRegex(self.rows, r"\$\{esc\(log\." + field + r"\b")

	def test_an_empty_log_is_the_kits_empty_panel(self):
		self.assertRegex(self.rows, r"state\.render\(list,\s*\"empty\"")


class TestThePagesStayInTheDesk(unittest.TestCase):
	def test_no_hard_coded_app_paths(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertNotRegex(_code(path), r"['\"`(]/app/")

	def test_no_history_writes_were_added(self):
		for name in ("rental-planner", "maintenance-day-board", "stripe-payments-dashboard",
				"quickbooks-online-dashboard", "water-engineering-wizard"):
			with self.subTest(page=name):
				code = _code(PAGES[name])
				for token in ("pushState", "replaceState", "popstate"):
					self.assertNotIn(token, code)


if __name__ == "__main__":
	unittest.main()
