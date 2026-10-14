"""Links that keep your place: page state in the address (custom pages S5a, v1.662.0).

Three Desk pages keep what they show in the address's query, so a reload, a pasted link and Back
and Forward all land on the same state:

* Maintenance Day Board — ``?date=YYYY-MM-DD``; the bare address is today and follows the clock.
* Rental Planner — ``?start=YYYY-MM-DD``, the board's first day; the bare address is today.
* Location Timeline — ``?employee=&from_date=&to_date=``, the trail on screen; the bare address
  is no one's trail.

What the v16 router does, and so what each page must do (``git show
origin/version-16:frappe/public/js/frappe/router.js``):

* ``parse()`` copies the address's query into ``frappe.route_options`` on every route change,
  merged into what is there, and ``set_route(page, {k: v})`` puts ``k`` there too. So a page reads
  its keys from ``route_options`` and consumes them as the list view does (delete each key, null
  an emptied object); a key left behind is replayed by the next plain ``set_route`` to the page.
* Before v16.50 ``set_route`` pushes the path alone (the query only decides whether to push), so
  routing to the page on screen to change its query loses the query: Plan a Trip, v1.520.0. Each
  page writes its own address instead, on ``window.location.pathname`` (the router may have put a
  workspace shell in front of the page name), ``pushState`` for a state the person chose and
  ``replaceState`` to correct the entry on screen, and only when the address does not already
  say it.
* A malformed date in an address is ignored, quietly: it must not throw or put up a message.

The behaviour runs against a model of the router (both sides of v16.50) in
``scripts/test_desk_page_history.js`` (Day Board, Location Timeline) and
``scripts/test_rental_planner_history.js``. This pins the shape those harnesses rely on, and the
Day Board's server side. Bench-free: reads files only, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_page_deep_links
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DAY_BOARD = APP / "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js"
PLANNER = APP / "asset_management/page/rental_planner/rental_planner.js"
TIMELINE = APP / "workforce/page/location_timeline/location_timeline.js"
BOARD_API = APP / "api/maintenance_board.py"

# Per page: the key(s) it keeps, the function that consumes them, and the one address writer.
PAGES = {
	"maintenance-day-board": {
		"path": DAY_BOARD,
		"keys": ("date",),
		"take": ("function takeRouteDate() {", "function addressFor(date) {"),
		"write": ("function writeAddress(push) {", "function drawNav() {"),
		"valid": "function validDay(value) {",
	},
	"rental-planner": {
		"path": PLANNER,
		"keys": ("start",),
		"take": ("take_route_start() {", "board_route() {"),
		"write": ("write_board_address(push) {", "go(route, replace) {"),
		"valid": "function rp_valid_day(value) {",
	},
	"location-timeline": {
		"path": TIMELINE,
		"keys": ("employee", "from_date", "to_date"),
		"take": ("takeRouteOptions() {", "init() {"),
		"write": ("writeAddress(push) {", "async setSilently(field, value) {"),
		"valid": "function validDay(value) {",
	},
}


def _code(path):
	"""The script with comments stripped: the comments explain every rule below in prose, and an
	absence assertion must not be satisfied (or broken) by its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _between(src, start, end):
	at = src.index(start)
	return src[at : src.index(end, at + len(start))]


class TestEachPageConsumesItsRouteOptions(unittest.TestCase):
	def test_the_keys_are_deleted_and_an_emptied_object_nulled(self):
		for name, page in PAGES.items():
			take = _between(_code(page["path"]), *page["take"])
			with self.subTest(page=name):
				self.assertIn("frappe.route_options", take)
				self.assertIn("hasOwnProperty.call(opts,", take)
				self.assertRegex(take, r"delete opts(\.\w+|\[key\]);")
				self.assertIn("if (!Object.keys(opts).length) frappe.route_options = null;", take)
				for key in page["keys"]:
					self.assertIn(key, take)

	def test_no_page_reads_the_address_query_instead(self):
		"""After an in-app set_route on an older router the query is not in the address at all;
		route_options is where both arrive."""
		for name, page in PAGES.items():
			code = _code(page["path"])
			with self.subTest(page=name):
				self.assertNotIn("get_url_arg", code)
				self.assertNotIn("new URLSearchParams(window.location.search)", code)
				self.assertNotIn("new URLSearchParams(location.search)", code)


class TestEachPageWritesItsOwnAddress(unittest.TestCase):
	def test_one_writer_on_the_path_the_router_put_there(self):
		for name, page in PAGES.items():
			code = _code(page["path"])
			write = _between(code, *page["write"])
			with self.subTest(page=name):
				self.assertEqual(code.count("history.pushState("), 1)
				self.assertEqual(code.count("history.replaceState("), 1)
				self.assertRegex(write, r"history\.pushState\(null, ['\"]{2}, url\)")
				self.assertRegex(write, r"history\.replaceState\(window\.history\.state, ['\"]{2}, url\)")
				self.assertIn("window.location.pathname", write)
				# Nothing written when the address already says it: no duplicate entry.
				self.assertIn("if (url === window.location.pathname + window.location.search) return;", write)

	def test_never_routes_to_itself_to_change_the_query(self):
		"""set_route to the page on screen with new options is the v1.520.0 bug: the old router
		pushes the path alone and the query is lost."""
		self.assertNotRegex(_code(DAY_BOARD), r"set_route\(\s*['\"]maintenance-day-board")
		self.assertNotIn("set_route(RP.route, {", _code(PLANNER))
		self.assertNotIn("set_route(PAGE_ROUTE, {", _code(TIMELINE))

	def test_no_hard_coded_app_paths(self):
		for name, page in PAGES.items():
			with self.subTest(page=name):
				self.assertNotRegex(_code(page["path"]), r"['\"`(]/app/")


class TestDatesAreValidated(unittest.TestCase):
	def test_a_calendar_check_not_only_a_pattern(self):
		"""2026-02-30 matches the pattern and is not a day; Date.UTC rolls it into March."""
		for name, page in PAGES.items():
			code = _code(page["path"])
			# The function is a few lines long; its first 600 characters hold all of it.
			valid = code[code.index(page["valid"]) :][:600]
			with self.subTest(page=name):
				self.assertIn("Date.UTC(", valid)
				self.assertIn("getUTCMonth() === +m[2] - 1", valid)
				self.assertIn("getUTCDate() === +m[3]", valid)


class TestTheDayBoardServerTakesADay(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.src = BOARD_API.read_text(encoding="utf-8")
		cls.tree = ast.parse(cls.src)
		cls.funcs = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}

	def test_date_is_optional_so_today_is_unchanged(self):
		fn = self.funcs["get_day_board_data"]
		self.assertEqual([a.arg for a in fn.args.args], ["date"])
		self.assertEqual(len(fn.args.defaults), 1)
		self.assertIsNone(fn.args.defaults[0].value)
		# The assistant tool and every older caller send nothing.
		body = ast.get_source_segment(self.src, fn)
		self.assertIn("return _board(today, None)", body)

	def test_permission_is_checked_before_the_day_is_read(self):
		body = ast.get_source_segment(self.src, self.funcs["get_day_board_data"])
		self.assertLess(body.index("frappe.PermissionError"), body.index("_board_day(date)"))

	def test_the_day_is_strict_iso(self):
		body = ast.get_source_segment(self.src, self.funcs["_board_day"])
		self.assertIn("datetime.date.fromisoformat(", body)
		self.assertIn("escape_html", body)

	def test_the_sql_takes_the_day_as_a_bound_parameter(self):
		body = ast.get_source_segment(self.src, self.funcs["_board"])
		self.assertIn("%(day)s", body)
		self.assertIn('{"day": day, "next_day": next_day}', body)

	def test_the_page_asks_for_a_day_only_when_it_is_not_today(self):
		code = _code(DAY_BOARD)
		self.assertIn("date ? { date } : {}", code)


if __name__ == "__main__":
	unittest.main()
