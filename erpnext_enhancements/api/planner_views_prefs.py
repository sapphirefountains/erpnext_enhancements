"""Saved views per person for both planners, and the chrome of "Print this view" (Phase 6C).

TASK-2026-02469. Nik, 2026-10-09: each person's filters, view and grouping are remembered ("Field
crew, Build only") and the planner opens where they left off, **on whatever device they open it
on**. So a view lives on the server, per user, never in the browser.

* **Where it is stored.** Frappe's own per-user settings table, ``__UserSettings`` (one row per
  ``(user, doctype)``, a JSON ``data`` column), under the keys ``Project Planner`` and
  ``Maintenance Planner`` (and, since form kit P6.8, ``Opportunity Board``: the board's filter bar,
  view and table sort, gated by read permission on Opportunity rather than a planner role) and
  inside one top-level key of the row, ``planner_views``. Frappe's
  ``frappe.model.utils.user_settings.save`` was the obvious tool and is deliberately not used: it
  writes only to the Redis cache, and ``sync_user_settings`` copies the cache to the table later.
  A production deploy ``FLUSHDB``s that cache (CLAUDE.md), so a view saved a few minutes before a
  merge would vanish. These endpoints write the row straight to the table in the request's own
  transaction, then drop any cached copy of it so the sync can never write a stale one back.
* **What a view holds** (:data:`SCHEMA`): the calendar view, the page's filters, the color-by and
  whether Resources available is open. Keys not in the schema are dropped, enums must be one of
  their values, text is trimmed and capped, and a view's JSON is capped at
  :data:`MAX_VIEW_BYTES` before it is even parsed. A person keeps at most :data:`MAX_VIEWS` named
  views; saving a name that exists (case aside) replaces it.
* **"Last used"** is a view of its own (``last``): the page saves it a moment after anything
  changes and applies it when the planner opens. The URL always wins over it (the page's job, see
  ``PP6C_METHODS``). Phase 4's *Running over* chip is never part of it, because a chip that quietly
  hides most of the board the next morning is a trap; a named view may hold it, since saving one is
  a deliberate act.
* **Print chrome.** :func:`get_print_chrome` hands the page the print design system's letterhead,
  stripes and ruled-table cell styles (``print_style``, the same chrome as the weekly crew sheet).
  The page builds the body from the data it already has on screen, with its filters, and prints it
  from a browser window: the server PDF is broken on production (``docs/pdf-generation.md``).
* **The first-visit tour** (P12 of the planner UX redesign, ``docs/planner-ux-redesign.md``). Whether a
  person has been shown each planner's tour is kept in the same row, under a key of its own
  (``planner_tour``), as ``{"version": 1, "seen": {"desktop": bool, "phone": bool}}``: a desktop and a
  phone land on different screens, so each has its own tour, shown once. :func:`get_views` carries the
  flags (``tour``), so the page needs no request of its own to know; :func:`mark_tour_seen` sets one. The
  page marks a tour the moment it shows it (the guided page's first-time pop-up works the same way), so
  a reload never repeats it. Never frappe's Redis-only user_settings, which a deploy flushes, and never
  the browser alone: a person shown the tour on one machine is not shown it again on the next.

Every endpoint is gated by the calling planner's own roles (``project_planner._require_planner`` or
``maintenance_planner._require_planner``) and acts only on ``frappe.session.user``'s row.
"""

import json
import re

import frappe
from frappe import _

#: The planner a request is about -> its key in ``__UserSettings.doctype``. ``board`` is the
#: Opportunity Board (form kit P6.8, v1.663.0), which keeps its saved views in this same store.
PLANNERS = {"project": "Project Planner", "maintenance": "Maintenance Planner", "board": "Opportunity Board"}
#: The one top-level key of the row these endpoints own; anything else in the row is kept.
ROW_KEY = "planner_views"
STORE_VERSION = 1
#: The first-visit tour's flags (P12), beside ``planner_views`` in the same row (see the module docstring).
TOUR_ROW_KEY = "planner_tour"
#: The planners that have a tour, and the two tours each has: a phone and a desktop land on different
#: screens. The pages' PPTOUR.kinds and MPTOUR.kinds say the same (tests/test_planner_tour.py).
TOUR_PLANNERS = ("project", "maintenance")
TOUR_KINDS = ("desktop", "phone")

MAX_VIEWS = 20
MAX_NAME = 60
MAX_TEXT = 140
#: A single view's JSON as sent, before parsing.
MAX_VIEW_BYTES = 4000
#: The whole row (``__UserSettings.data`` is a TEXT column: 64 KB).
MAX_ROW_BYTES = 32000
MAX_PRINT_LINES = 8
MAX_PRINT_TEXT = 200

PROJECT_GROUPS = ("", "Field", "PM", "Design", "Subcontractor")
PROJECT_COLOR_MODES = ("default", "person", "job_type", "project", "pm", "status")
MAINTENANCE_COLOR_MODES = ("default", "technician", "site", "status", "contract")
#: The Opportunity Board's views, layouts and table columns (public/js/opportunity_board/board.js
#: VIEWS, LAYOUTS, SORT_KEYS; tests/test_opportunity_board_focus.py holds the two lists equal).
BOARD_VIEWS = ("board", "table", "timeline", "attention")
BOARD_LAYOUTS = ("stages", "streams")
BOARD_SORT_KEYS = (
	"title",
	"stage",
	"amount",
	"probability",
	"weighted",
	"closing",
	"days",
	"owner",
	"complete",
)

#: What a view may hold, per planner: ``key -> ("bool",) | ("text",) | ("enum", values)``. The pages'
#: own constants are checked against this by tests/test_planner_phase6c.py.
SCHEMA = {
	"project": {
		# "heatmap" is what a view saved before the planner UX redesign's P7 says for Capacity (the Heatmap and
		# Utilization tabs merged into the Crew tab's Capacity screen); the page treats it as Capacity.
		# P5: the Lookahead is a tab a view can name, and the home tab.
		"view": ("enum", ("week", "month", "crew", "heatmap", "capacity", "lookahead")),
		"project": ("text",),
		"pm": ("text",),
		"group": ("enum", PROJECT_GROUPS),
		"foreign": ("bool",),
		"over_only": ("bool",),
		"panel": ("bool",),
		"color_by": ("enum", PROJECT_COLOR_MODES),
		# Whether color_by was picked rather than left at the page's default (Job type, since the planner
		# UX redesign). An older "last used" view stores "default" (Task color) either way, so the page
		# honors a saved "default" only when this is set.
		"color_chosen": ("bool",),
		# Show all tasks (P2 of the planner UX redesign, D6): off, the default, is phase and crew work only.
		"all_tasks": ("bool",),
	},
	"maintenance": {
		"view": ("enum", ("month", "week", "crew")),
		"technician": ("text",),
		"projected": ("bool",),
		"project_work": ("bool",),
		# P10 of the planner UX redesign: project work as cards (off: the busy strip, D13) and Resources
		# available open (it starts collapsed). "resources" replaces "panel", which the old default (open) put
		# in every user's "last used" row, so a stored "panel" must not hold the panel open against the new
		# default: the page no longer reads it, and the next save drops it.
		"project_cards": ("bool",),
		"resources": ("bool",),
		"color_by": ("enum", MAINTENANCE_COLOR_MODES),
	},
	# The board's filter bar, view and table sort (board.js VIEW_SCHEMA, key for key).
	"board": {
		"view": ("enum", BOARD_VIEWS),
		"layout": ("enum", BOARD_LAYOUTS),
		"mine": ("bool",),
		"owner": ("text",),
		"stream": ("text",),
		"min_amount": ("text",),
		"closing_month": ("bool",),
		"show_snoozed": ("bool",),
		"hold_open": ("bool",),
		"sort": ("enum", BOARD_SORT_KEYS),
		"desc": ("bool",),
		# v1.684.0: what a card shows beside its title (board.js card_parts / card_text): "" is the
		# card as it always was, "none" nothing, else a comma list of board.js CARD_PARTS.
		"card": ("text",),
	},
}
#: Never remembered as "last used" (see the module docstring).
NOT_REMEMBERED = ("over_only",)

#: The print eyebrow and the print design system pillar of each planner.
PRINT_EYEBROW = {
	"project": "Project Planner",
	"maintenance": "Maintenance Planner",
	"board": "Opportunity Board",
}
PRINT_PILLAR = {"project": None, "maintenance": "service", "board": None}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")


# ---------------------------------------------------------------------- pure helpers


def clean_text(value, limit=MAX_TEXT):
	"""A string with control characters removed, whitespace collapsed and trimmed, at most ``limit``."""
	if value is None:
		return ""
	if isinstance(value, bool) or not isinstance(value, str | int | float):
		return ""
	text = _SPACES.sub(" ", _CONTROL.sub(" ", str(value))).strip()
	return text[:limit].strip()


def as_flag(value):
	"""True/False from what a browser sends for a checkbox (a bool, 1/0, "1"/"0", "true")."""
	if isinstance(value, bool):
		return value
	if isinstance(value, int | float):
		return bool(value)
	return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def clean_name(value):
	"""A view's name, or a refusal when nothing is left of it."""
	name = clean_text(value, MAX_NAME)
	if not name:
		frappe.throw(_("Give the view a name."))
	return name


def parse_view(data):
	"""A view as sent (JSON text or a dict) -> a dict, refusing anything too large or not an object."""
	if isinstance(data, dict):
		raw = json.dumps(data)
	else:
		raw = data if isinstance(data, str) else ""
	if len(raw.encode("utf-8")) > MAX_VIEW_BYTES:
		frappe.throw(_("That view is too large to save."))
	if isinstance(data, dict):
		return data
	try:
		parsed = json.loads(raw or "{}")
	except ValueError:
		frappe.throw(_("That view could not be read."))
	if not isinstance(parsed, dict):
		frappe.throw(_("That view could not be read."))
	return parsed


def clean_view(planner, data, remembered=False):
	"""Only the keys :data:`SCHEMA` allows for ``planner``, each of the right type.

	An enum value that is not one of its values is dropped rather than refused, so a view saved by an
	older page still applies what it can. ``remembered`` (the "last used" view) also drops
	:data:`NOT_REMEMBERED`.
	"""
	schema = SCHEMA.get(planner) or {}
	out = {}
	for key, rule in schema.items():
		if not isinstance(data, dict) or key not in data:
			continue
		if remembered and key in NOT_REMEMBERED:
			continue
		value = data[key]
		kind = rule[0]
		if kind == "bool":
			out[key] = as_flag(value)
		elif kind == "enum":
			if isinstance(value, str) and value in rule[1]:
				out[key] = value
		elif kind == "text":
			if value is None or (isinstance(value, str | int | float) and not isinstance(value, bool)):
				out[key] = clean_text(value)
	return out


def read_store(planner, row):
	"""The ``planner_views`` part of a ``__UserSettings`` row, cleaned again on the way out.

	Re-cleaned because the row outlives the schema: a key a later page stops knowing about must not
	come back to it, and a hand-edited row must not reach the page as anything but a clean view.
	"""
	raw = row.get(ROW_KEY) if isinstance(row, dict) else None
	raw = raw if isinstance(raw, dict) else {}
	last = raw.get("last")
	views = []
	seen = set()
	for entry in raw.get("views") or []:
		if not isinstance(entry, dict):
			continue
		name = clean_text(entry.get("name"), MAX_NAME)
		if not name or name.lower() in seen:
			continue
		seen.add(name.lower())
		views.append(
			{
				"name": name,
				"data": clean_view(planner, entry.get("data") if isinstance(entry.get("data"), dict) else {}),
				"saved_on": clean_text(entry.get("saved_on"), 40),
			}
		)
	return {
		"last": clean_view(planner, last, remembered=True) if isinstance(last, dict) and last else None,
		"views": views[:MAX_VIEWS],
	}


def put_view(views, name, data, saved_on):
	"""``views`` with ``name`` saved (replacing one of the same name, case aside), sorted by name.

	Refuses a new name past :data:`MAX_VIEWS`; replacing an existing one is always allowed.
	"""
	rest = [v for v in views if v["name"].lower() != name.lower()]
	if len(rest) >= MAX_VIEWS:
		frappe.throw(_("You can keep {0} saved views. Delete one first.").format(MAX_VIEWS))
	rest.append({"name": name, "data": data, "saved_on": saved_on})
	return sorted(rest, key=lambda v: v["name"].lower())


def print_meta(lines, printed_on=None):
	"""The right-hand block under the print title: each line escaped, joined with line breaks."""
	from erpnext_enhancements import print_style as ps

	out = []
	for line in (lines or [])[:MAX_PRINT_LINES]:
		text = clean_text(line, MAX_PRINT_TEXT)
		if text:
			out.append(ps.escape_html(text))
	if printed_on:
		out.append(ps.escape_html(_("Printed {0}").format(printed_on)))
	return "<br>".join(out)


# ---------------------------------------------------------------------- the row


def _require(planner):
	"""The ``__UserSettings`` key for ``planner``, after the planner's own role gate."""
	planner = clean_text(planner, 20)
	if planner not in PLANNERS:
		frappe.throw(_("Unknown planner."))
	if planner == "project":
		from erpnext_enhancements.api import project_planner

		project_planner._require_planner()
	elif planner == "board":
		# The board shows what the caller may read of Opportunity (get_board reads through
		# get_list), so its views are for anyone who may read one.
		frappe.has_permission("Opportunity", "read", throw=True)
	else:
		from erpnext_enhancements.api import maintenance_planner

		maintenance_planner._require_planner()
	return planner, PLANNERS[planner]


def _read_row(doctype, for_update=False):
	"""The caller's whole ``__UserSettings`` row for ``doctype`` as a dict ({} when there is none)."""
	query = "select `data` from `__UserSettings` where `user`=%s and `doctype`=%s"
	if for_update:
		query += " for update"
	rows = frappe.db.sql(query, (frappe.session.user, doctype))
	text = rows[0][0] if rows and rows[0] else None
	if not text:
		return {}
	try:
		data = json.loads(text)
	except (TypeError, ValueError):
		return {}
	return data if isinstance(data, dict) else {}


def _write_row(doctype, row):
	"""Write the caller's row straight to the table, then forget any cached copy of it."""
	text = json.dumps(row, separators=(",", ":"), sort_keys=True, default=str)
	if len(text.encode("utf-8")) > MAX_ROW_BYTES:
		frappe.throw(_("Your saved views are too large to store. Delete one first."))
	user = frappe.session.user
	frappe.db.multisql(
		{
			"mariadb": "INSERT INTO `__UserSettings` (`user`, `doctype`, `data`) VALUES (%s, %s, %s) "
			"ON DUPLICATE KEY UPDATE `data`=%s",
			"*": "INSERT INTO `__UserSettings` (`user`, `doctype`, `data`) VALUES (%s, %s, %s) "
			"ON CONFLICT (`user`, `doctype`) DO UPDATE SET `data`=%s",
		},
		(user, doctype, text, text),
	)
	try:
		# frappe's own reader caches the row here and sync_user_settings writes the cache back to the
		# table; a copy cached before this write must not overwrite it later.
		frappe.cache.hdel("_user_settings", f"{doctype}::{user}")
	except Exception:
		pass


def _store_into(row, store):
	row = dict(row or {})
	row[ROW_KEY] = {"version": STORE_VERSION, "last": store.get("last"), "views": store.get("views") or []}
	return row


def read_tour(row):
	"""Which tours the row says were shown: ``{"desktop": bool, "phone": bool}``.

	Only a real ``true`` counts, so a hand-edited row (``"yes"``, ``1``) or a damaged one reads as not
	seen, which shows the tour once more rather than never.
	"""
	raw = row.get(TOUR_ROW_KEY) if isinstance(row, dict) else None
	seen = raw.get("seen") if isinstance(raw, dict) else None
	seen = seen if isinstance(seen, dict) else {}
	return {kind: seen.get(kind) is True for kind in TOUR_KINDS}


def _tour_into(row, seen):
	row = dict(row or {})
	row[TOUR_ROW_KEY] = {"version": STORE_VERSION, "seen": {kind: bool(seen.get(kind)) for kind in TOUR_KINDS}}
	return row


def _now_text():
	return str(frappe.utils.now_datetime())[:19]


# ---------------------------------------------------------------------- endpoints


@frappe.whitelist()
def get_views(planner):
	"""The caller's views for ``planner`` (``project`` | ``maintenance``).

	``{"planner", "last": {view} | None, "views": [{"name", "data": {view}, "saved_on"}], "max_views",
	"tour": {"desktop": bool, "phone": bool}}``. ``tour`` says which of the planner's first-visit tours
	the caller has been shown (P12); the Opportunity Board has none, so it reads ``{}`` there.
	"""
	planner, doctype = _require(planner)
	row = _read_row(doctype)
	store = read_store(planner, row)
	return {
		"planner": planner,
		"last": store["last"],
		"views": store["views"],
		"max_views": MAX_VIEWS,
		"tour": read_tour(row) if planner in TOUR_PLANNERS else {},
	}


@frappe.whitelist(methods=["POST"])
def save_view(planner, name, data):
	"""Save ``data`` as the caller's view called ``name`` (replacing one of the same name).

	``{"saved": name, "views": [...]}``
	"""
	planner, doctype = _require(planner)
	name = clean_name(name)
	view = clean_view(planner, parse_view(data))
	row = _read_row(doctype, for_update=True)
	store = read_store(planner, row)
	store["views"] = put_view(store["views"], name, view, _now_text())
	_write_row(doctype, _store_into(row, store))
	return {"saved": name, "views": store["views"]}


@frappe.whitelist(methods=["POST"])
def delete_view(planner, name):
	"""Delete the caller's view called ``name`` (case aside). ``{"deleted": bool, "views": [...]}``"""
	planner, doctype = _require(planner)
	name = clean_text(name, MAX_NAME)
	row = _read_row(doctype, for_update=True)
	store = read_store(planner, row)
	kept = [v for v in store["views"] if v["name"].lower() != name.lower()]
	deleted = len(kept) != len(store["views"])
	if deleted:
		store["views"] = kept
		_write_row(doctype, _store_into(row, store))
	return {"deleted": deleted, "views": kept}


@frappe.whitelist(methods=["POST"])
def save_last_view(planner, data):
	"""Remember ``data`` as the view the caller last used on ``planner``. ``{"last": {view}}``"""
	planner, doctype = _require(planner)
	view = clean_view(planner, parse_view(data), remembered=True)
	row = _read_row(doctype, for_update=True)
	store = read_store(planner, row)
	if store["last"] != view:
		store["last"] = view
		_write_row(doctype, _store_into(row, store))
	return {"last": view}


@frappe.whitelist(methods=["POST"])
def mark_tour_seen(planner, kind):
	"""The caller has been shown ``planner``'s ``kind`` tour (``desktop`` | ``phone``); never show it again.

	Only the caller's own row, and only the flag asked for: the other tour and the saved views are kept.
	Setting it twice writes nothing the second time. ``{"planner", "tour": {"desktop": bool, "phone": bool}}``
	"""
	# Before the role gate: the board's gate is a read on Opportunity, which says nothing about a tour.
	if clean_text(planner, 20) not in TOUR_PLANNERS:
		frappe.throw(_("That planner has no tour."))
	planner, doctype = _require(planner)
	kind = clean_text(kind, 20)
	if kind not in TOUR_KINDS:
		frappe.throw(_("Unknown tour."))

	def mark():
		row = _read_row(doctype, for_update=True)
		seen = read_tour(row)
		if not seen[kind]:
			seen[kind] = True
			_write_row(doctype, _tour_into(row, seen))
		return seen

	try:
		seen = mark()
	except frappe.QueryDeadlockError:
		# A person's first save of anything on a planner makes their row, and two requests making it at once
		# (the tour's mark and the "last used" view, a second apart at the soonest) can pick a deadlock victim
		# under REPEATABLE READ. This request has written nothing else, so it starts again, once. The page
		# needs no more of it: a mark that still fails is told to the browser instead (PPTOUR.tour_mark).
		frappe.db.rollback()
		seen = mark()
	return {"planner": planner, "tour": seen}


@frappe.whitelist()
def get_print_chrome(planner, title=None, lines=None):
	"""The print design system's chrome for one printed view of ``planner``.

	``title`` and ``lines`` (a JSON list of strings: the range, the filters, the color-by) are escaped
	here. Returns ``{"open", "close", "th", "td"}``: ``open`` is the stripe, letterhead and title,
	``close`` the running line and the bottom stripe, ``th``/``td`` the ruled-table cell styles. The
	same chrome as the weekly crew sheet (``project_planner.crew_sheet_document``).
	"""
	planner, _doctype = _require(planner)
	from erpnext_enhancements import print_style as ps

	if isinstance(lines, str):
		try:
			lines = json.loads(lines or "[]")
		except ValueError:
			lines = [lines]
	if not isinstance(lines, list):
		lines = []
	pillar = PRINT_PILLAR.get(planner)
	printed_on = frappe.utils.format_datetime(frappe.utils.now_datetime(), "MMM d, yyyy h:mm a")
	heading = clean_text(title, MAX_PRINT_TEXT) or _(PRINT_EYEBROW[planner])
	return {
		"open": ps.page_open(pillar)
		+ ps.letterhead(
			pillar,
			ps.escape_html(_(PRINT_EYEBROW[planner])),
			ps.escape_html(heading),
			print_meta(lines, printed_on),
		),
		"close": ps.page_close(pillar),
		"th": ps.th(pillar),
		"td": ps.TD,
	}


# ---------------------------------------------------------------------- the guided page's view (v1.684.0)
#
# Nik, 2026-10-14: a saved record on the guided page is laid out the way each person chooses (one
# page, tabs or step by step; where the steps sit; the summary; notes and comments; pinned sections;
# hidden fields; text size; focus mode; presets ...), "saved to their instance and not change it
# for everyone else's views". The same store as the planners' views and for the same reason: a row
# of the caller's own ``__UserSettings``, written straight to the table (never frappe's Redis-only
# user_settings, which every deploy flushes), here under ``Guided Entry`` / ``guided_view``:
#
#     {"version": 1, "intro_seen": bool, "doctypes": {"Opportunity": {entry}}}
#
# An entry is ``prefs`` (enums, GUIDED_SCHEMA), ``pinned``, ``landing``, ``hidden_fields``,
# ``hidden_steps``, ``summary_fields``, ``presets`` and ``last`` (the last section per record). The
# page's twin is public/js/form_kit/view.js (VIEW_SCHEMA, clean_entry), held equal by
# tests/test_guided_view_settings.py. Everything is cleaned on the way in and again on the way out:
# an unknown key is dropped, an unknown value falls back to its default. Only the caller's own row
# is ever read or written, so one person's choices never reach anyone else's page.

GUIDED_SETTINGS = "Guided Entry"
GUIDED_ROW_KEY = "guided_view"
#: ``key -> values``; the first value is the default (view.js VIEW_SCHEMA, key for key).
GUIDED_SCHEMA = {
	"layout": ("page", "tabs", "steps"),
	"nav": ("left", "right", "top"),
	"summary": ("right", "left", "hidden"),
	"notes": ("below", "side", "collapsed"),
	"tools": ("side", "below", "collapsed"),
	"density": ("comfortable", "compact"),
	"hints": ("shown", "hidden"),
	"fields": ("all", "filled"),
	"text": ("normal", "large", "larger"),
	"contrast": ("normal", "high"),
	"focus": ("off", "on"),
}
GUIDED_LANDINGS = ("last", "first")
#: The caps, view.js MAX_* one for one.
GUIDED_MAX = {
	"pinned": 20,
	"hidden": 200,
	"summary_fields": 12,
	"presets": 10,
	"preset_name": 40,
	"last": 50,
	"doctypes": 20,
}
#: The parts of an entry ``save_guided_view`` replaces when sent (``last`` has its own endpoint).
GUIDED_PARTS = ("prefs", "pinned", "landing", "hidden_fields", "hidden_steps", "summary_fields", "presets")
#: One save's JSON as sent, before parsing.
MAX_GUIDED_BYTES = 12000
_GUIDED_KEY = re.compile(r"^[A-Za-z0-9_]{1,140}$")


def clean_guided_prefs(data):
	"""Every key of :data:`GUIDED_SCHEMA`, each one of its values (the default when it was not)."""
	source = data if isinstance(data, dict) else {}
	out = {}
	for key, values in GUIDED_SCHEMA.items():
		value = source.get(key)
		out[key] = value if isinstance(value, str) and value in values else values[0]
	return out


def _guided_keys(raw, limit):
	"""Step keys or fieldnames: strings of the allowed shape, each once, at most ``limit``."""
	out = []
	for value in raw if isinstance(raw, list) else []:
		key = value.strip() if isinstance(value, str) else ""
		if _GUIDED_KEY.match(key) and key not in out and len(out) < limit:
			out.append(key)
	return out


def _guided_landing(raw):
	value = raw.strip() if isinstance(raw, str) else ""
	return value if value in GUIDED_LANDINGS or _GUIDED_KEY.match(value) else "last"


def _guided_presets(raw):
	out = []
	for preset in raw if isinstance(raw, list) else []:
		if not isinstance(preset, dict):
			continue
		name = clean_text(preset.get("name"), GUIDED_MAX["preset_name"])
		if not name or any(p["name"].lower() == name.lower() for p in out):
			continue
		if len(out) >= GUIDED_MAX["presets"]:
			break
		out.append({"name": name, "prefs": clean_guided_prefs(preset.get("prefs"))})
	return out


def _guided_last(raw):
	out = []
	for pair in raw if isinstance(raw, list) else []:
		if not isinstance(pair, list | tuple) or len(pair) < 2:
			continue
		name = clean_text(pair[0])
		step = pair[1].strip() if isinstance(pair[1], str) else ""
		if not name or not _GUIDED_KEY.match(step) or any(p[0] == name for p in out):
			continue
		if len(out) >= GUIDED_MAX["last"]:
			break
		out.append([name, step])
	return out


def clean_guided_entry(raw):
	"""One DocType's entry, every part cleaned (view.js ``clean_entry``)."""
	r = raw if isinstance(raw, dict) else {}
	summary = r.get("summary_fields")
	summary = _guided_keys(summary, GUIDED_MAX["summary_fields"]) if isinstance(summary, list) else None
	return {
		"prefs": clean_guided_prefs(r.get("prefs")),
		"pinned": _guided_keys(r.get("pinned"), GUIDED_MAX["pinned"]),
		"landing": _guided_landing(r.get("landing")),
		"hidden_fields": _guided_keys(r.get("hidden_fields"), GUIDED_MAX["hidden"]),
		"hidden_steps": _guided_keys(r.get("hidden_steps"), GUIDED_MAX["hidden"]),
		"summary_fields": summary,
		"presets": _guided_presets(r.get("presets")),
		"last": _guided_last(r.get("last")),
	}


def read_guided_store(row):
	"""The ``guided_view`` part of a ``__UserSettings`` row, cleaned: ``{"intro_seen", "doctypes"}``."""
	raw = row.get(GUIDED_ROW_KEY) if isinstance(row, dict) else None
	raw = raw if isinstance(raw, dict) else {}
	stored = raw.get("doctypes") if isinstance(raw.get("doctypes"), dict) else {}
	doctypes = {}
	for doctype, entry in stored.items():
		name = clean_text(doctype)
		if name and len(doctypes) < GUIDED_MAX["doctypes"]:
			doctypes[name] = clean_guided_entry(entry)
	return {"intro_seen": raw.get("intro_seen") is True, "doctypes": doctypes}


def _guided_into(row, store):
	row = dict(row or {})
	row[GUIDED_ROW_KEY] = {
		"version": STORE_VERSION,
		"intro_seen": bool(store["intro_seen"]),
		"doctypes": store["doctypes"],
	}
	return row


def _guided_doctype(doctype):
	"""A DocType the guided page serves (form_kit.config.CONFIGS) that the caller may read."""
	from erpnext_enhancements.form_kit.config import CONFIGS

	name = clean_text(doctype)
	if name not in CONFIGS:
		frappe.throw(_("There is no guided view for {0}.").format(name or "?"))
	frappe.has_permission(name, "read", throw=True)
	return name


def _guided_parse(data):
	"""What a save sent (JSON text or a dict) -> a dict, refusing anything too large or not an object."""
	raw = json.dumps(data) if isinstance(data, dict) else (data if isinstance(data, str) else "")
	if len(raw.encode("utf-8")) > MAX_GUIDED_BYTES:
		frappe.throw(_("Those view settings are too large to save."))
	if isinstance(data, dict):
		return data
	try:
		parsed = json.loads(raw or "{}")
	except ValueError:
		frappe.throw(_("Those view settings could not be read."))
	if not isinstance(parsed, dict):
		frappe.throw(_("Those view settings could not be read."))
	return parsed


def _guided_entry(store, doctype):
	if doctype not in store["doctypes"] and len(store["doctypes"]) >= GUIDED_MAX["doctypes"]:
		frappe.throw(_("Too many guided views are stored."))
	return store["doctypes"].get(doctype) or clean_guided_entry({})


@frappe.whitelist()
def get_guided_view():
	"""The caller's guided-page view settings: ``{"intro_seen": bool, "doctypes": {doctype: entry}}``."""
	return read_guided_store(_read_row(GUIDED_SETTINGS))


@frappe.whitelist(methods=["POST"])
def save_guided_view(doctype, data):
	"""Replace the parts of the caller's entry for ``doctype`` that ``data`` sends (:data:`GUIDED_PARTS`).

	``{"doctype", "entry"}``, the entry as stored.
	"""
	doctype = _guided_doctype(doctype)
	sent = _guided_parse(data)
	row = _read_row(GUIDED_SETTINGS, for_update=True)
	store = read_guided_store(row)
	entry = dict(_guided_entry(store, doctype))
	for part in GUIDED_PARTS:
		if part in sent:
			entry[part] = sent[part]
	entry = clean_guided_entry(entry)
	store["doctypes"][doctype] = entry
	_write_row(GUIDED_SETTINGS, _guided_into(row, store))
	return {"doctype": doctype, "entry": entry}


@frappe.whitelist(methods=["POST"])
def remember_guided_section(doctype, name, step):
	"""The section the caller last had open on record ``name``: newest first, at most 50 records."""
	doctype = _guided_doctype(doctype)
	record = clean_text(name)
	step = step.strip() if isinstance(step, str) else ""
	if not record or not _GUIDED_KEY.match(step):
		frappe.throw(_("That section could not be remembered."))
	row = _read_row(GUIDED_SETTINGS, for_update=True)
	store = read_guided_store(row)
	entry = dict(_guided_entry(store, doctype))
	entry["last"] = _guided_last([[record, step]] + [pair for pair in entry["last"] if pair[0] != record])
	store["doctypes"][doctype] = entry
	_write_row(GUIDED_SETTINGS, _guided_into(row, store))
	return {"name": record, "step": step}


@frappe.whitelist(methods=["POST"])
def mark_guided_intro_seen():
	"""The first-time explanation has been shown to the caller; it is never shown again."""
	row = _read_row(GUIDED_SETTINGS, for_update=True)
	store = read_guided_store(row)
	if not store["intro_seen"]:
		store["intro_seen"] = True
		_write_row(GUIDED_SETTINGS, _guided_into(row, store))
	return {"intro_seen": True}
