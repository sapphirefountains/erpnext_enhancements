"""Browser Back and Forward on six Desk pages.

The product rule: Back returns to the previous step, screen, tab or view of a page, Forward
restores it, Back from a page's first screen leaves it as it always did, and Back or Forward
never silently loses unsaved work. On the Desk, Frappe v16's router owns ``popstate``: it
re-renders whatever route the browser lands on and fires ``on_page_show``. So a view that Back
should return to has to be a route segment, and these pages now carry one:

* Device Console — ``device-console/camera`` and ``/employee`` (the two sheets). Back closes a
  sheet and stays on the console; Forward opens it again, or steps back off an entry whose sheet
  cannot be opened again (a pick already made, or one the device's status no longer allows).
  Only an entry the page pushed, marked in ``history.state``, is a sheet's: the same URL from
  another page becomes the console, and pushed over the console's own entry while it was
  showing, it is stepped back off, as is a marked entry after a reload. A replace the page asks
  for is cleared as soon as set_route has read it, not left to set_route's own late reset. A
  scan is never an entry, and an unknown scan's enroll prompt waits for the console to be showing.
* Inventory Scanner Audit — ``inventory-scanner-audit/camera`` and ``/find``, the same way. A
  camera read is looked up on the camera's own entry and then either steps back off it or hands
  it to Find Item, so a read never pushes; a failed lookup steps off once frappe's message has
  closed (Bootstrap's own state, since the X leaves ``is_visible`` set); a lookup's reply opens
  Find Item only if the clerk has not moved since the scan; and a camera read's reply acts only
  on its own entry, never on a camera the clerk has opened since.
* Sales Pipeline — ``sales-pipeline/tv`` is TV mode, and nothing else is. It used to stick
  (``tv || state.tv_mode``), leaving the chrome-less TV view on the plain board after Back.
* QuickBooks Record Matching — ``/transactions`` is the Parked transactions tab.
* Question Review — ``training-review/course/<course>`` and ``/lesson/<lesson>``. A Back or
  Forward that comes while a verdict is in flight is held until the last one lands. A "Stay"
  that keeps an edit holds only while the route names the view it was said to.
* Location Timeline — ``location-timeline/live`` is Live; the bare route is Trail.

``scripts/test_desk_page_history.js`` runs the real page scripts against a model of the v16
router, Dialog and Bootstrap modal and a fake session history, and is run from here. The static
checks below pin what it relies on and what it cannot see. Bench-free: no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_desk_page_history
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_desk_page_history.js"

PAGES = {
	"device-console": APP / "device_management/page/device_console/device_console.js",
	"inventory-scanner-audit": APP / "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js",
	"sales-pipeline": APP / "crm_enhancements/page/sales_pipeline/sales_pipeline.js",
	"quickbooks-record-matching": APP / "quickbooks_online/page/quickbooks_record_matching/quickbooks_record_matching.js",
	"training-review": APP / "training/page/training_review/training_review.js",
	"location-timeline": APP / "workforce/page/location_timeline/location_timeline.js",
}
SCANNERS = ("device-console", "inventory-scanner-audit")
# A scanner's show steps back, rather than replacing, onto its own entry from a sheet URL pushed
# over it while it was showing, or from a sheet entry of its own on the first show after a reload.
STEP_BACK = "if (stayed || (first && this.ownSheet(sheet))) {"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: the comments explain every rule below in prose, and
	an absence assertion must not be satisfied (or broken) by its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _between(src, start, end):
	at = src.index(start)
	return src[at : src.index(end, at + len(start))]


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	"""The scenarios themselves: taps, reads, Back and Forward against the modelled router."""

	def test_the_history_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)

	def test_every_page_script_parses(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				result = subprocess.run([shutil.which("node"), "--check", str(path)], capture_output=True, text=True)
				self.assertEqual(result.returncode, 0, result.stderr)


class TestNoPageWritesHistoryBehindTheRouter(unittest.TestCase):
	"""Frappe's router owns the Desk's history. A page that pushes an entry of its own gets a
	``popstate`` the router re-renders as whatever route the URL still names."""

	def test_no_push_state_of_a_pages_own(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertNotIn("history.pushState", _code(path))
				self.assertNotIn("popstate", _code(path))
				self.assertNotIn("beforeunload", _code(path))

	def test_replace_state_only_marks_an_entry_the_page_pushed(self):
		"""The one direct history write: a mark in `history.state` on the entry set_route has
		just pushed, with no URL. The review page marks a lesson, so leaving it can step back
		rather than add an entry; the scanners mark a sheet, so a show can tell their own sheet
		entry from the same URL reached from another page. Frappe's router never reads
		`history.state`."""
		marks = {
			"training-review": 'window.history.replaceState({ tq_opened: view.lesson }, "");',
			"device-console": (
				"window.history.replaceState(Object.assign({}, window.history.state, { dc_sheet: name }), '');"
			),
			"inventory-scanner-audit": (
				"window.history.replaceState(Object.assign({}, window.history.state, { isa_sheet: name }), '');"
			),
		}
		for name, path in PAGES.items():
			code = _code(path)
			with self.subTest(page=name):
				if name in marks:
					self.assertEqual(code.count("history.replaceState("), 1)
					self.assertIn(marks[name], code)
				else:
					self.assertNotIn("history.replaceState(", code)

	def test_no_hard_coded_app_paths(self):
		"""v16's link handler routes only /desk paths in place (router.js is_app_route); an
		/app href is a full page load, and Back from it reloads the page from scratch."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertNotRegex(_code(path), r"['\"`(]/app/")


class TestScannerSheetsAreRouteSegments(unittest.TestCase):
	def test_the_sheet_segments(self):
		expected = {
			"device-console": "const DC_SHEETS = ['camera', 'employee'];",
			"inventory-scanner-audit": "const ISA_SHEETS = ['camera', 'find'];",
		}
		for name in SCANNERS:
			with self.subTest(page=name):
				self.assertIn(expected[name], _code(PAGES[name]))

	def test_on_page_show_is_the_sheet_aware_show(self):
		for name in SCANNERS:
			with self.subTest(page=name):
				code = _code(PAGES[name])
				show = _between(code, f"frappe.pages['{name}'].on_page_show = function (wrapper) {{", "};")
				self.assertIn(".onShow();", show)
				self.assertNotIn("focusScan", show)

	def test_a_sheet_is_shown_only_after_its_route_settles(self):
		"""Every route change closes the open dialog (set_history and change_to), the one that
		pushes the sheet's own segment included."""
		for name in SCANNERS:
			with self.subTest(page=name):
				block = _between(_code(PAGES[name]), "openSheet(name, show, reopen) {", "hideSheet(d) {")
				self.assertRegex(block, r"const settled = frappe\.set_route\([A-Z]+_ROUTE, name\);")
				self.assertIn("settled.then(() => {", block)
				then = block[block.index("settled.then(") :]
				self.assertIn("if (this.sheetRoute() === name && !this.sheet) show();", then)

	def test_each_sheet_entry_is_marked_as_it_is_pushed(self):
		"""set_route writes the entry before it returns, so the mark goes on straight after it:
		on the sheet's own entry, before a Back could move off it."""
		for name in SCANNERS:
			with self.subTest(page=name):
				block = _between(_code(PAGES[name]), "openSheet(name, show, reopen) {", "hideSheet(d) {")
				self.assertLess(block.index("frappe.set_route("), block.index("this.markSheet(name);"))
				self.assertLess(block.index("this.markSheet(name);"), block.index("settled.then("))
				self.assertEqual(_code(PAGES[name]).count("this.markSheet("), 1)

	def test_the_camera_starts_after_the_sheet_is_up(self):
		for name in SCANNERS:
			with self.subTest(page=name):
				camera = _between(_code(PAGES[name]), "showCamera(", "injectStyles() {")
				self.assertLess(camera.index("d.show();"), camera.index("getUserMedia("))

	def test_a_close_other_than_back_steps_back_off_the_sheets_entry(self):
		"""Three steps back, and no more: off a sheet closed by a read, a pick, X or Escape; off a
		sheet entry Back or Forward landed on that cannot be opened again; and off an unmarked
		sheet URL pushed over the page's own entry while it was showing."""
		for name in SCANNERS:
			with self.subTest(page=name):
				code = _code(PAGES[name])
				self.assertEqual(code.count("window.history.back();"), 3)
				self.assertIn("frappe.router.once('change', finish);", code)
				# ...and only while the route is still on that segment, so Back never double-steps.
				self.assertRegex(code, r"if \(this\.sheetRoute\(\) !== name\) \{\s*finish\(\);\s*return;")

	def test_a_modal_still_fading_in_is_closed_once_shown(self):
		"""Bootstrap 4.6 ignores hide() during the fade-in, and cur_dialog is not set yet, so
		the router's own dialog-closing cannot see a sheet Back pressed early should close."""
		for name in SCANNERS:
			with self.subTest(page=name):
				self.assertIn("else d.$wrapper.one('shown.bs.modal', () => d.hide());", _code(PAGES[name]))

	def test_a_sheet_url_the_page_did_not_push_never_starts_the_camera(self):
		"""A reload, a pasted link, or the same URL reached from another page (the awesome bar
		offers routes with a second segment as frequent links): the entry becomes the page's own.
		Reopening the sheet there would put another page behind it, for X or a read to step
		back onto."""
		for name in SCANNERS:
			with self.subTest(page=name):
				show = _between(_code(PAGES[name]), "onShow() {", "reopenSheet(name) {")
				opener = "if (first || !this.ownSheet(sheet)) {"
				self.assertIn(opener, show)
				replace = _between(show, opener, "} else if")
				self.assertLess(replace.index("frappe.route_flags.replace_route = true;"), replace.index("frappe.set_route("))
				self.assertNotIn("reopenSheet(", replace)
				# ...and it is decided before anything could reopen the sheet.
				self.assertLess(show.index(opener), show.index("this.reopenSheet(sheet)"))

	def test_an_unmarked_sheet_url_pushed_over_the_page_steps_back_rather_than_replacing(self):
		"""The awesome bar's link to a sheet, picked on the page itself, is pushed over the page's
		own entry. Replacing it left two identical entries in a row, so the next Back seemed to do
		nothing. The page was showing if its last show was its own entry and no other page has been
		shown since: frappe triggers "hide" on the page it leaves, and only the wrapper's own counts
		(a Bootstrap "hide.bs.*" from inside the page bubbles up to it too). An unmarked first show,
		and an arrival from another page, still replace."""
		for name in SCANNERS:
			with self.subTest(page=name):
				code = _code(PAGES[name])
				ctor = _between(code, "constructor(page, wrapper) {", "\n\t}")
				self.assertIn("this.away = false;", ctor)
				self.assertIn("$(wrapper).on('hide', (e) => {", ctor)
				self.assertIn("if (e.target === wrapper) this.away = true;", ctor)
				self.assertNotIn(".away = true", code.replace("if (e.target === wrapper) this.away = true;", ""))
				show = _between(code, "onShow() {", "reopenSheet(name) {")
				stayed = "const stayed = !first && !this.away && came === null;"
				self.assertIn(stayed, show)
				self.assertLess(show.index(stayed), show.index("this.away = false;"))
				self.assertLess(show.index("this.away = false;"), show.index("if (sheet) {"))
				handed = _between(show, "if (first || !this.ownSheet(sheet)) {", "} else if")
				self.assertIn(STEP_BACK, handed)
				step = _between(handed, STEP_BACK, "} else {")
				self.assertIn("window.history.back();", step)
				self.assertNotIn("set_route(", step)
				self.assertNotIn("replace_route", step)
				self.assertIn("frappe.route_flags.replace_route = true;", handed[handed.index("} else {") :])

	def test_a_reload_on_the_pages_own_sheet_entry_steps_back_rather_than_replacing(self):
		"""history.state survives a reload (and Android restoring a discarded tab), so a sheet entry
		the page marked is still marked on the first show that follows. The entry behind a marked
		one is always the page's own: only the page marks entries, and only ones it pushed over its
		own. Replacing it with the page's route left two identical entries in a row, so the next
		Back seemed to do nothing. A first show on an unmarked sheet URL (a pasted link, a bookmark)
		still replaces: there is no telling what is behind it."""
		for name in SCANNERS:
			with self.subTest(page=name):
				show = _between(_code(PAGES[name]), "onShow() {", "reopenSheet(name) {")
				handed = _between(show, "if (first || !this.ownSheet(sheet)) {", "} else if")
				self.assertIn(STEP_BACK, handed)
				# The only way into the replace is an unmarked first show or an arrival from elsewhere.
				replace = handed.index("frappe.route_flags.replace_route = true;")
				self.assertLess(handed.index(STEP_BACK), replace)

	def test_every_replace_the_page_asks_for_is_cleared_once_set_route_returns(self):
		"""v16's set_route reads `route_flags.replace_route` synchronously, in push_state, but resets
		route_flags only in its promise's `finally`, after a 100 ms timer and `frappe.after_ajax`,
		which waits until no request at all is in flight. On a first show the page's bootstrap call
		is, so the flag outlived the page's replace and turned the clerk's next tap into a replace of
		the page's own entry: X, or Back, from the sheet it opened then left the page. Each flag the
		page sets must be followed by its set_route, and at once by the reset."""
		flag, reset = "frappe.route_flags.replace_route = true;", "frappe.route_flags.replace_route = false;"
		for name in SCANNERS:
			with self.subTest(page=name):
				lines = [line.strip() for line in _code(PAGES[name]).splitlines() if line.strip()]
				sets = [i for i, line in enumerate(lines) if line.endswith(flag)]
				self.assertEqual(len(sets), 2)
				for at in sets:
					call = next(i for i in range(at + 1, len(lines)) if "frappe.set_route(" in lines[i])
					self.assertEqual(call, at + 1, lines[at : call + 1])
					self.assertEqual(lines[call + 1], reset, lines[call : call + 2])
				self.assertEqual(lines.count(reset), len(sets))

	def test_a_sheet_that_cannot_reopen_steps_back_rather_than_replacing(self):
		"""Back or Forward onto a sheet's entry that cannot be opened again: replacing it with
		the page's own route left two identical entries in a row, so the next Back seemed to do
		nothing. The entry behind a sheet's is always the page's own, so step back onto it."""
		for name in SCANNERS:
			with self.subTest(page=name):
				show = _between(_code(PAGES[name]), "onShow() {", "reopenSheet(name) {")
				bounce = _between(show, "} else if (!this.reopenSheet(sheet)) {", "return;")
				self.assertIn("window.history.back();", bounce)
				self.assertNotIn("set_route(", bounce)
				self.assertNotIn("replace_route", bounce)

	def test_a_scan_is_never_a_history_entry(self):
		resolved_at = {
			"device-console": ("onResolved(res, code) {", "renderDevice() {"),
			"inventory-scanner-audit": ("onResolved(res, code, search) {", "addCount() {"),
		}
		for name in SCANNERS:
			with self.subTest(page=name):
				code = _code(PAGES[name])
				start, end = resolved_at[name]
				scan = _between(code, "handleScan(raw", start)
				resolved = _between(code, start, end)
				for block in (scan, resolved):
					self.assertNotIn("frappe.set_route(", block)

	def test_an_unknown_codes_find_item_opens_only_where_the_scan_was_made(self):
		"""A lookup's reply that lands after Back, Forward, a sheet tapped open or another Desk
		page must not route: it dragged the clerk back to the scanner from wherever they went, or
		pushed an entry with no tap since the last (which Chrome skips on Back)."""
		code = _code(PAGES["inventory-scanner-audit"])
		self.assertIn("this.shows += 1;", _between(code, "onShow() {", "reopenSheet(name) {"))
		scan = _between(code, "handleScan(raw", "unknownOpensSearch(res) {")
		self.assertLess(scan.index("const shows = this.shows;"), scan.index("this.call('resolve_scan'"))
		reply = scan[scan.index("this.call('resolve_scan'") :]
		for guard in (
			"this.shows === shows &&",
			"(frappe.get_route() || [])[0] === ISA_ROUTE &&",
			"(!fromCamera || (own && this.sheetRoute() === 'camera'))",
			"if (fromCamera && !(search && this.unknownOpensSearch(res))) {",
			"this.onResolved(res, code, search);",
		):
			self.assertIn(guard, reply)
		resolved = _between(code, "onResolved(res, code, search) {", "addCount() {")
		self.assertIn("if (search && this.unknownOpensSearch(res)) {", resolved)
		self.assertEqual(code.count("this.openItemSearch(shown);"), 1)

	def test_a_camera_reads_reply_acts_only_on_the_entry_the_read_was_taken_on(self):
		"""A camera the clerk opened before the reply landed (on the read's entry, or on a new one
		after Back) has taken that entry over, as has a later read. Stepping off then closed the
		clerk's camera, and an unknown code replaced it with Find Item for the old code."""
		code = _code(PAGES["inventory-scanner-audit"])
		scan = _between(code, "handleScan(raw", "unknownOpensSearch(res) {")
		self.assertIn("const read = fromCamera ? ++this.reads : 0;", scan)
		self.assertIn("const mine = () => fromCamera && this.leftover === 'camera' && this.reads === read;", scan)
		# Only a read counts, and openSheet is what hands the entry on: it clears `leftover`.
		self.assertEqual(code.count("++this.reads"), 1)
		opener = _between(code, "openSheet(name, show, reopen) {", "hideSheet(d) {")
		self.assertEqual(opener.count("this.leftover = null;"), 2)
		reply = scan[scan.index("this.call('resolve_scan'") :]
		self.assertIn("const own = mine();", reply)
		not_mine = _between(reply, "if (!own) {", "}")
		self.assertIn("this.onResolved(res, code);", not_mine)
		self.assertNotIn("leaveSheet(", not_mine)
		self.assertLess(reply.index("if (!own) {"), reply.index("this.leaveSheet('camera', () => this.onResolved(res, code));"))

	def test_an_unknown_devices_enroll_prompt_needs_the_console_showing(self):
		code = _code(PAGES["device-console"])
		resolved = _between(code, "onResolved(res, code) {", "renderDevice() {")
		self.assertLess(resolved.index("if (!this.onConsole()) {"), resolved.index("frappe.confirm("))
		on_console = _between(code, "onConsole() {", "onResolved(res, code) {")
		self.assertIn("(frappe.get_route() || [])[0] === DC_ROUTE && !this.sheetRoute() && !this.sheet && !this.opening", on_console)

	def test_the_picker_checks_out_the_device_it_opened_for(self):
		"""Not whatever a scan still being looked up puts on the card while the picker is open."""
		code = _code(PAGES["device-console"])
		for opener, call in (
			("pickEmployeeAndCheckOut(reopen) {", "this.act('check_out', { device, employee })"),
			("pickEmployeeAndTransfer(reopen) {", "this.act('transfer', { device, new_employee: employee })"),
		):
			with self.subTest(opener=opener):
				block = _between(code, opener, "\n\t}")
				self.assertIn("const device = this.state.device.name;", block)
				self.assertIn(call, block)

	def test_forward_never_offers_a_pick_already_made_or_no_longer_allowed(self):
		"""Forward onto the picker's entry after a Check Out opened a second check-out of an
		Assigned device (which the server refuses), and after a Transfer a second transfer."""
		code = _code(PAGES["device-console"])
		sheet = _between(code, "showEmployeeSheet(onPick) {", "openCamera(reopen) {")
		onhide = sheet[sheet.index("d.onhide = () => {") :]
		self.assertLess(onhide.index("if (picked) app.pick = null;"), onhide.index("app.sheetClosed('employee'"))
		reopen = _between(code, "reopenSheet(name) {", "buildSkeleton() {")
		self.assertIn("this.pickAllowed(pick.action, d)", reopen)
		allowed = _between(code, "pickAllowed(action, d) {", "\n\t}")
		self.assertIn("if (action === 'transfer') return d.status === 'Assigned';", allowed)
		self.assertIn("return d.status === 'In Stock' || d.status === 'In Repair';", allowed)
		# The same statuses the server accepts.
		api = _text(APP / "api/device_management.py")
		self.assertIn('if doc.status not in ("In Stock", "In Repair"):', _between(api, "def check_out(", "def "))
		self.assertIn('if doc.status != "Assigned":', _between(api, "def transfer(", "def "))

	def test_a_failed_camera_lookup_steps_off_once_frappes_message_closes(self):
		"""The camera's entry has no sheet on it once the read is taken. Stepping off it while
		frappe's error message is up would close the message; never stepping off left a dead
		entry, so the clerk's next Back changed nothing on screen."""
		code = _code(PAGES["inventory-scanner-audit"])
		scan = _between(code, "handleScan(raw", "unknownOpensSearch(res) {")
		failed = scan[scan.index("() => {", scan.index("this.onResolved(res, code, search);")) :]
		self.assertIn("if (!fromCamera) return;", failed)
		wait = failed[failed.index("this.afterFrappeMessage(() => {") :]
		# ...and only off the read's own entry, if nothing has taken it over since.
		self.assertLess(wait.index("if (!mine()) return;"), wait.index("this.leaveSheet('camera');"))
		after = _between(code, "afterFrappeMessage(then) {", "unknownOpensSearch(res) {")
		self.assertIn("[frappe.msg_dialog, frappe.error_dialog]", after)
		self.assertIn(".one('hidden.bs.modal',", after)

	def test_whether_frappes_message_is_up_is_bootstraps_own_state(self):
		"""Not frappe's `is_visible`, which only Dialog.hide() clears. The dialog's X is
		`data-dismiss="modal"` (v16 dom.js), which Bootstrap 4.6 closes with no hide() around it,
		and msgprint's dialog is one for the whole Desk session. After any message was closed by
		its X, a camera lookup that failed with no dialog up (a dropped connection: request.js has
		no handler for status 0) waited for a "hidden" that never came, and the camera's entry was
		never stepped off. Bootstrap's `_isShown` is set as show() starts, so it is true through
		the fade-in, and cleared by every hide, the X's included."""
		code = _code(PAGES["inventory-scanner-audit"])
		after = _between(code, "afterFrappeMessage(then) {", "unknownOpensSearch(res) {")
		self.assertIn("d.$wrapper.data('bs.modal')", after)
		self.assertIn("modal._isShown", after)
		self.assertNotIn("is_visible", code)

	def test_the_record_links_route_in_place(self):
		self.assertIn(
			"href=\"${frappe.utils.get_form_link('Managed Device', d.name)}\"", _code(PAGES["device-console"])
		)
		self.assertIn(
			"frappe.utils.get_form_link('Stock Reconciliation', res.stock_reconciliation)",
			_code(PAGES["inventory-scanner-audit"]),
		)


class TestSalesPipelineTvModeIsTheRoute(unittest.TestCase):
	def setUp(self):
		self.code = _code(PAGES["sales-pipeline"])

	def test_the_button_routes(self):
		self.assertIn('frappe.set_route(state.tv_mode ? ["sales-pipeline"] : ["sales-pipeline", "tv"]);', self.code)
		self.assertNotIn("function set_tv_mode", self.code)

	def test_the_route_alone_decides_the_mode(self):
		show = _between(self.code, 'frappe.pages["sales-pipeline"].on_page_show = function (wrapper) {', "\n};")
		self.assertIn("state.tv_mode = tv;", show)
		self.assertNotIn("state.tv_mode = tv ||", show)

	def test_fullscreen_is_asked_for_in_the_click_before_routing(self):
		button = _between(self.code, 'page.add_inner_button(__("TV Mode"), () => {', 'document.addEventListener("fullscreenchange"')
		self.assertLess(button.index("requestFullscreen()"), button.index("frappe.set_route("))

	def test_leaving_the_page_ends_the_fullscreen_it_asked_for(self):
		show = _between(self.code, 'frappe.pages["sales-pipeline"].on_page_show = function (wrapper) {', "\n};")
		self.assertIn('.off("hide.sales_pipeline_tv")', show)
		self.assertIn("sales_pipeline_exit_fullscreen();", show[show.index('.one("hide.sales_pipeline_tv"') :])
		self.assertIn("document.fullscreenElement === document.documentElement", self.code)


if __name__ == "__main__":
	unittest.main()
