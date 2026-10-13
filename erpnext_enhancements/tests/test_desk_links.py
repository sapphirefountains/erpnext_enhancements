"""No hard-coded ``/app/`` navigation in anything that renders inside the Desk.

On Frappe v16 the Desk lives at ``/desk``. ``/app/...`` still works, but only through a server
redirect, so an in-Desk ``href="/app/..."`` (or ``window.location = "/app/..."``) forces a FULL
page load — the whole Desk boots again — and Back reloads it a second time. v16's link handler
routes only ``/desk`` paths in place (``router.js`` ``is_app_route``). Use
``frappe.utils.get_form_link`` / ``frappe.set_route`` / a ``/desk/<slug>`` href instead.

What is scanned (comments stripped from JS first, so an explanation may still say ``/app/``):

* every Desk page, report and doctype script, every Custom HTML Block, and every script under
  ``public/js`` EXCEPT the www-only directories named in ``WWW_ONLY_DIRS``. The set is a glob
  minus that list, so a new file is covered with no edit here;
* the server modules whose returned URLs a Desk page, block or form renders as links;
* every ``url`` / ``link_to`` in the shipped Workspace JSON (a URL shortcut or link).

Deliberately NOT scanned, and why: absolute links in emails, SMS, digests and Google Calendar
bodies are built with ``get_url("/app/...")`` — a link opened from outside the browser is a full
load either way, and ``/app`` redirects fine; ``www/`` pages and the kiosk / feedback / wall /
stock-scan apps link into the Desk from a website page, which is a full load by nature;
``product_feedback/code_anchors.py`` *parses* ``/app`` URLs.

Bench-free: no ``frappe`` import. Run: python -m unittest erpnext_enhancements.tests.test_desk_links
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

# A hard-coded /app/ path after a quote, backtick or opening paren — the same expression
# test_desk_page_history.test_no_hard_coded_app_paths uses.
APP_PATH = re.compile(r"""['"`(]/app/""")
# In a Python string *value* the opening quote is not part of the value, so a value that IS a
# path starts with it; an HTML fragment carries the quote (`href="/app/...`).
APP_PATH_IN_VALUE = re.compile(r"""(?:^|['"`(=])/app/""")

# public/js directories loaded only by a website page, never in the Desk: a link from there
# into the Desk is a full load by nature. (Each is a www app with its own bundle or template.)
WWW_ONLY_DIRS = {
	"feedback": "the /feedback www app (feedback.bundle.js, referenced only by www/feedback.html)",
	"kiosk": "the /kiosk www app (time clock)",
	"wall": "the /wall www app",
	"stock_scan": "the /stock-scan www app (stock_scan.bundle.js)",
}

# Server modules whose returned URLs a Desk page, Custom HTML Block or form renders as links.
# (api/integrations_health.py -> integrations_health.js; api/device_dashboard.py ->
# device_fleet_dashboard.js; calendar_sync/time_off.py's review_route -> the Calendar Sync
# Settings form.) Add a module here when a new server function hands a Desk page a URL.
DESK_SERVER_MODULES = [
	"api/integrations_health.py",
	"api/device_dashboard.py",
	"project_enhancements/calendar_sync/time_off.py",
	"task_enhancements/doctype/task/task.py",
]

# An (A)-class file that cannot change yet. Path (relative to the app) -> reason. Every entry is
# checked to STILL violate, so a fixed or deleted file fails the build until its entry is
# removed here: this list can only shrink.
ALLOWED = {
}


def _rel(path):
	return path.relative_to(APP).as_posix()


def _text(path):
	return path.read_text(encoding="utf-8")


def _code_lines(path):
	"""[(line number, text)] with JS comments blanked, so line numbers stay true."""
	src = _text(path)
	src = re.sub(r"/\*[\s\S]*?\*/", lambda m: re.sub(r"[^\n]", "", m.group()), src)
	return [
		(n, "" if line.strip().startswith("//") else line) for n, line in enumerate(src.splitlines(), 1)
	]


def _js_hits(path):
	return [f"{_rel(path)}:{n}: {line.strip()[:110]}" for n, line in _code_lines(path) if APP_PATH.search(line)]


def _py_hits(path):
	"""Every string literal (and every constant part of an f-string) that is an /app/ path."""
	hits = []
	for node in ast.walk(ast.parse(_text(path))):
		if isinstance(node, ast.Constant) and isinstance(node.value, str):
			if APP_PATH_IN_VALUE.search(node.value):
				hits.append(f"{_rel(path)}:{node.lineno}: {node.value.strip()[:110]!r}")
	return sorted(set(hits))


def _desk_script_files():
	files = set()
	for pattern in ("*/page/*/*.js", "*/page/*/*.html", "*/report/*/*.js", "*/doctype/*/*.js"):
		files.update(APP.glob(pattern))
	files.update(APP.glob("custom_html_blocks/*.js"))
	files.update(APP.glob("custom_html_blocks/*.html"))
	for path in (APP / "public" / "js").rglob("*.js"):
		if _rel(path).split("/")[2] in WWW_ONLY_DIRS:
			continue
		files.add(path)
	return sorted(files)


def _workspace_url_hits(path):
	hits = []

	def walk(value):
		if isinstance(value, dict):
			for key, item in value.items():
				if key in ("url", "link_to") and isinstance(item, str) and re.match(r"/app(/|$)", item):
					hits.append(f"{_rel(path)}: {key}={item!r}")
				walk(item)
		elif isinstance(value, list):
			for item in value:
				walk(item)

	walk(json.loads(_text(path)))
	return hits


def _unexpected(hits_by_file):
	"""The hits in files that are not in ALLOWED."""
	return [h for rel, hits in hits_by_file.items() if rel not in ALLOWED for h in hits]


class TestNoHardCodedAppPathsInTheDesk(unittest.TestCase):
	def test_the_scan_sees_the_files(self):
		files = {_rel(p) for p in _desk_script_files()}
		# A glob that silently matched nothing would pass forever.
		self.assertGreater(len(files), 150)
		for expected in (
			"custom_html_blocks/projects_dashboard.js",
			"google_drive/page/drive_link_manager/drive_link_manager.js",
			"public/js/crm_enhancements/opportunity.js",
			"plaid_banking/doctype/plaid_banking_settings/plaid_banking_settings.js",
		):
			self.assertIn(expected, files)
		self.assertFalse([f for f in files if f.startswith("public/js/feedback/")])

	def test_www_only_dirs_exist(self):
		for name in WWW_ONLY_DIRS:
			self.assertTrue((APP / "public" / "js" / name).is_dir(), name)

	def test_no_desk_script_hard_codes_an_app_path(self):
		hits_by_file = {}
		for path in _desk_script_files():
			hits = _js_hits(path)
			if hits:
				hits_by_file[_rel(path)] = hits
		unexpected = _unexpected(hits_by_file)
		self.assertEqual(
			unexpected,
			[],
			"A Desk script hard-codes /app/ (a full page load on v16). Use "
			"frappe.utils.get_form_link / frappe.set_route / a /desk/<slug> href:\n" + "\n".join(unexpected),
		)

	def test_no_server_url_rendered_in_the_desk_is_an_app_path(self):
		hits_by_file = {}
		for rel in DESK_SERVER_MODULES:
			hits = _py_hits(APP / rel)
			if hits:
				hits_by_file[rel] = hits
		unexpected = _unexpected(hits_by_file)
		self.assertEqual(
			unexpected,
			[],
			"A server function hands a Desk page an /app/ URL; build /desk/ instead:\n"
			+ "\n".join(unexpected),
		)

	def test_the_kiosk_hands_the_visit_wizard_a_desk_path(self):
		"""The kiosk opens these in a new tab; /desk/visit-wizard/<record> is the page's own
		address (review_digest.py builds the same), so it skips the /app redirect."""
		hits = [h for h in _py_hits(APP / "api" / "time_kiosk.py") if "visit-wizard" in h]
		self.assertEqual(hits, [])

	def test_workspace_url_shortcuts_are_desk_paths(self):
		hits = []
		for path in sorted(APP.glob("*/workspace/*/*.json")):
			hits.extend(_workspace_url_hits(path))
		self.assertEqual(
			hits,
			[],
			"A Workspace URL shortcut/link hard-codes /app/ (a full Desk reload on click):\n"
			+ "\n".join(hits),
		)

	def test_the_desk_shortcuts_block_routes_desk_urls_in_place(self):
		code = "\n".join(line for _, line in _code_lines(APP / "custom_html_blocks" / "desk_shortcuts.js"))
		self.assertIn("frappe.set_route(...segments)", code)
		self.assertRegex(code, r"\(desk\|app\)")

	def test_allowed_entries_are_still_needed(self):
		stale = []
		for rel in ALLOWED:
			path = APP / rel
			if not path.exists():
				stale.append(f"{rel}: file no longer exists")
				continue
			hits = _py_hits(path) if rel.endswith(".py") else _js_hits(path)
			if not hits:
				stale.append(f"{rel}: no /app/ path left")
		self.assertEqual(stale, [], "Remove these from ALLOWED:\n" + "\n".join(stale))

	def test_the_guard_catches_what_it_is_for(self):
		self.assertTrue(APP_PATH.search('<a href="/app/task/T-1">'))
		self.assertTrue(APP_PATH.search("`/app/${slug}/x`"))
		self.assertTrue(APP_PATH.search("get_full_url('/app/contact/x')"))
		self.assertFalse(APP_PATH.search('<a href="/desk/task/T-1">'))
		self.assertFalse(APP_PATH.search("const rel = u.replace(/^\\/(desk|app)\\/?/i, '')"))
		self.assertTrue(APP_PATH_IN_VALUE.search("/app/managed-device?x=1"))
		self.assertTrue(APP_PATH_IN_VALUE.search('<a href="/app/task/T-1">'))
		self.assertFalse(APP_PATH_IN_VALUE.search("/desk/managed-device"))
		self.assertFalse(APP_PATH_IN_VALUE.search("a path like /application/x"))


if __name__ == "__main__":
	unittest.main()
