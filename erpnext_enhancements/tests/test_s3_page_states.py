"""Custom pages S3.1 (v1.645.0, TASK-2026-02820): seven Desk pages draw their states from sf_kit.

Each of these pages sat on "Loading…" for good, or went blank, when its server call failed:

* Rental Planner — the fleet timeline stayed on "Loading the fleet...".
* Maintenance Day Board — a minimal line from S1.1, and its 60 s timer passed frappe.call's
  ``silent``, which stops only the msgprint of a 417: a wall screen still stacked a "Server Error"
  dialog every minute while the server answered 5xx.
* Stripe Payments dashboard — no loading or error state at all.
* QuickBooks Online dashboard — no error handling, and the sync-log rows went into markup unescaped.
* Water Engineering Wizard — four calls, none with an error path.
* Device Console and Inventory Scanner Audit — the start-up call had no error path.

Now each gets sf_kit from ONE shared loader, ``window.sf_kit_ready()`` in
``public/js/sf_kit_loader.js`` (imported by the global bundle): a memoized
``frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"])`` that falls back to a plain stand-in
when the kit never arrives (v16's loader resolves on a failed file). A page guards with
``typeof window.sf_kit_ready === "function"`` and otherwise requires the kit itself (a Desk opened
before the loader shipped). Each renders the kit's loading panel, and on a failed load an error
panel whose "Try again" runs the load again (or the no-access panel on a 403). A background
refresh uses the kit's quiet path, so frappe puts up no dialog for it.

The behaviour is run too, against the REAL kit as well as the stand-in:
``scripts/test_s3_page_states.mjs`` (the Day Board, Stripe, QuickBooks and Water pages, run by
TestTheRealKitHarness below), ``scripts/test_rental_planner_history.js`` and
``scripts/test_desk_page_history.js`` (the Rental Planner and both scanner pages). These are the
static checks. Bench-free: the scripts are read as text, comments stripped.

Run: python -m unittest erpnext_enhancements.tests.test_s3_page_states
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
LOADER = APP / "public/js/sf_kit_loader.js"
GLOBAL_BUNDLE = APP / "public/js/erpnext_enhancements.bundle.js"
HARNESS = REPO / "scripts/test_s3_page_states.mjs"

PAGES = {
	"rental-planner": APP / "asset_management/page/rental_planner/rental_planner.js",
	"maintenance-day-board": APP / "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js",
	"stripe-payments-dashboard": APP
	/ "stripe_payments/page/stripe_payments_dashboard/stripe_payments_dashboard.js",
	"quickbooks-online-dashboard": APP
	/ "quickbooks_online/page/quickbooks_online_dashboard/quickbooks_online_dashboard.js",
	"water-engineering-wizard": APP
	/ "water_engineering/page/water_engineering_wizard/water_engineering_wizard.js",
	"device-console": APP / "device_management/page/device_console/device_console.js",
	"inventory-scanner-audit": APP
	/ "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js",
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

	def test_every_page_goes_through_the_shared_loader(self):
		"""One loader for all seven, behind a guard for a Desk whose global bundle predates it."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertRegex(
					_code(path),
					r"""if \(typeof window\.sf_kit_ready === (['"])function\1\) return window\.sf_kit_ready\(\);""",
				)

	def test_no_page_carries_the_loaders_stand_in(self):
		"""The full stand-in lives in the loader; a page-local copy is the duplication it replaced."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertNotRegex(_code(path), r"(?i)plain_?kit")

	def test_the_no_loader_path_never_hands_the_page_undefined(self):
		"""No loader (an old Desk tab) and its old kit file gone too: window.sf_kit is unset after
		require, and a bare stand-in of at most a few lines answers instead of `undefined`."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				match = re.search(
					r"window\.sf_kit && window\.sf_kit\.state \? window\.sf_kit : (\w+)\(\)", code
				)
				self.assertTrue(match, "the no-loader path checks the kit arrived")
				lines = code[code.index(f"function {match.group(1)}() {{") :].splitlines()
				end = next(i for i, ln in enumerate(lines) if ln.strip() == "}")
				self.assertLessEqual(end - 1, 10, "a bare stand-in, not another copy of the loader's")
				self.assertIn("session_expired: (x && x.status) === 401", "\n".join(lines[:end]))


class TestTheSharedLoader(unittest.TestCase):
	def setUp(self):
		self.code = _code(LOADER)

	def test_it_installs_one_memoized_global(self):
		self.assertIn("window.sf_kit_ready = function () {", self.code)
		self.assertIn("if (!loading) {", self.code)
		self.assertEqual(len(REQUIRE.findall(self.code)), 1)

	def test_a_kit_that_never_arrived_resolves_to_the_stand_in(self):
		"""v16's loader resolves on a failed file, so the callback runs with no window.sf_kit."""
		self.assertIn("window.sf_kit && window.sf_kit.state ? window.sf_kit : plain_kit()", self.code)
		require_at = REQUIRE.search(self.code).start()
		self.assertIn("try {", self.code[max(0, require_at - 120) : require_at])
		self.assertIn("catch (e) {", self.code[require_at : require_at + 200])

	def test_the_stand_in_has_what_the_pages_use(self):
		stand_in = self.code[
			self.code.index("function plain_kit() {") : self.code.index("window.sf_kit_ready")
		]
		# chip and tiles since S3.2 (v1.649.0): the four dashboards draw their tiles through them.
		for piece in (
			"esc:",
			"call:",
			"state: {",
			"render(el, kind, opts) {",
			"toast:",
			"chip:",
			"tiles: { html:",
		):
			with self.subTest(piece=piece):
				self.assertIn(piece, stand_in)
		# A failed call rejects in the kit's own shape, read by rejection() below.
		self.assertIn("Promise.reject(rejection(xhr))", stand_in)

	def test_the_stand_in_reports_a_lapsed_session(self):
		"""Else the Day Board's timer would keep polling a 401 on the stand-in path."""
		rejection = self.code[
			self.code.index("function rejection(xhr) {") : self.code.index("function plain_kit() {")
		]
		for token in (
			"status === 401",
			'"SessionExpired"',
			'"AuthenticationError"',
			"object.session_expired",
			"user_id=",
		):
			with self.subTest(token=token):
				self.assertIn(token, rejection)
		self.assertIn("return { status, message, exc_type, session_expired: expired };", rejection)

	def test_the_global_bundle_imports_it_after_workspace_block_return(self):
		"""Its own line straight after workspace_block_return, so imports added at the end of the
		bundle by other work never conflict with it."""
		lines = [ln.strip() for ln in _code(GLOBAL_BUNDLE).splitlines() if ln.strip()]
		at = lines.index('import "./global_enhancements/workspace_block_return.js";')
		self.assertEqual(lines[at + 1], 'import "./sf_kit_loader.js";')
		self.assertEqual(sum(ln == 'import "./sf_kit_loader.js";' for ln in lines), 1)

	def test_the_kit_itself_stays_out_of_the_global_bundle(self):
		code = _code(GLOBAL_BUNDLE)
		self.assertNotIn("sf_kit.bundle", code)
		self.assertNotIn("sf_kit/index.js", code)


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
		refresh = code[code.index("function refresh(opts) {") : code.index("function stop() {")]
		self.assertIn("{ quiet: true }", refresh)
		# frappe.call is no longer how the board loads; the kit's quiet path is.
		self.assertNotIn("frappe.call({ method: 'erpnext_enhancements.api.maintenance_board", code)
		self.assertNotIn("silent: Boolean(", code)

	def _day_board_refresh(self):
		code = _code(PAGES["maintenance-day-board"])
		return code[code.index("function refresh(opts) {") : code.index("function stop() {")]

	def test_the_day_boards_failed_refresh_keeps_the_board(self):
		refresh = self._day_board_refresh()
		self.assertIn("if (!state.loaded) return failed(sf, err);", refresh)
		self.assertIn("$stale.text(", refresh)
		self.assertIn("sf.toast(", refresh)

	def test_the_day_board_draws_loading_on_the_first_attempt_only(self):
		"""A timer tick must not blank the board, nor the error panel of a board that never loaded."""
		refresh = self._day_board_refresh()
		self.assertIn("const first = !state.attempted;", refresh)
		self.assertIn("if (first || (opts && opts.asked)) sf.state.render($board, 'loading'", refresh)
		self.assertNotIn("if (!state.loaded) sf.state.render($board, 'loading'", refresh)

	def test_the_day_board_stops_polling_a_refusal(self):
		refresh = self._day_board_refresh()
		self.assertIn("if (err.status === 403 || err.session_expired) stop();", refresh)
		# Over a board already showing, a refusal says access is gone, not "could not refresh".
		self.assertIn("You no longer have access to the Day Board.", refresh)

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

	def test_the_qbo_resync_poll_retries_only_the_network_or_the_server(self):
		code = _code(PAGES["quickbooks-online-dashboard"])
		poll = code[code.index("function pollResyncPreview(") : code.index("function offerRunResync(")]
		self.assertIn("if (status === 0 || status >= 500) {", poll)
		self.assertEqual(
			poll.count("setTimeout(() => pollResyncPreview("), 2, "a running preview, and a hiccup"
		)
		self.assertIn("qboSignedOut(sf, qbo_root.find(", poll)

	def test_a_stale_design_reply_never_paints(self):
		code = _code(PAGES["water-engineering-wizard"])
		block = code[code.index("function load_design_state(") : code.index("function render_design_state(")]
		self.assertIn('const picked = () => $body.find("#we-design-pick").val() === design;', block)
		self.assertIn("if (picked()) render_design_state($state, message);", block)
		self.assertIn("if (!picked()) return;", block)

	def test_design_options_are_not_built_as_markup(self):
		code = _code(PAGES["water-engineering-wizard"])
		block = code[code.index("function load_design_list(") : code.index("function load_design_state(")]
		self.assertNotIn("<option value=", block)
		self.assertIn('$("<option></option>").val(d.name).text(label)', block)

	def test_the_scanner_start_up_has_its_own_slot(self):
		"""Its loading or error panel must not paint over the lines of a count already started."""
		code = _code(PAGES["inventory-scanner-audit"])
		boot = code[code.index("\tboot() {") : code.index("activeWarehouse() {")]
		self.assertIn("sf.state.render(this.$boot, 'loading'", boot)
		self.assertIn("isa_failed(sf, this.$boot, err", boot)
		self.assertNotIn("this.$list", boot)
		# A late reply sets the session only if the clerk has not started, cancelled or finished a
		# count since it was asked for (it would bring back a cancelled one).
		self.assertIn("const touched = this.sessionTouched;", boot)
		self.assertIn("if (this.sessionTouched === touched) this.state.session =", boot)
		self.assertEqual(code.count("this.sessionTouched += 1;"), 2, "startSession and resetSession")
		self.assertIn('<div class="isa-boot"></div>', code)

	def test_a_stale_timeline_reply_never_paints(self):
		code = _code(PAGES["rental-planner"])
		block = code[code.index("load_timeline() {") : code.index("timeline_failed(sf, $timeline, err) {")]
		self.assertGreaterEqual(block.count("if (token !== this._timeline_token) return;"), 3)


class TestQuickBooksLogRowsAreEscaped(unittest.TestCase):
	def setUp(self):
		code = _code(PAGES["quickbooks-online-dashboard"])
		self.rows = code[
			code.index("function renderLogs(sf, root, logs) {") : code.index("function connectQuickBooks()")
		]

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


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheRealKitHarness(unittest.TestCase):
	"""The Day Board, Stripe, QuickBooks and Water pages against the real sf_kit bundle."""

	def test_the_real_kit_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)

	def test_the_loader_parses(self):
		result = subprocess.run(
			[shutil.which("node"), "--check", str(LOADER)], capture_output=True, text=True
		)
		self.assertEqual(result.returncode, 0, result.stderr)


class TestThePagesStayInTheDesk(unittest.TestCase):
	def test_no_hard_coded_app_paths(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertNotRegex(_code(path), r"['\"`(]/app/")

	def test_no_history_writes_were_added(self):
		for name in (
			"rental-planner",
			"maintenance-day-board",
			"stripe-payments-dashboard",
			"quickbooks-online-dashboard",
			"water-engineering-wizard",
		):
			with self.subTest(page=name):
				code = _code(PAGES[name])
				for token in ("pushState", "replaceState", "popstate"):
					self.assertNotIn(token, code)


if __name__ == "__main__":
	unittest.main()
