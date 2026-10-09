"""Saved views per person for both planners, and the chrome of "Print this view" (Phase 6C).

TASK-2026-02469. Nik, 2026-10-09: each person's filters, view and grouping are remembered ("Field
crew, Build only") and the planner opens where they left off, **on whatever device they open it
on**. So a view lives on the server, per user, never in the browser.

* **Where it is stored.** Frappe's own per-user settings table, ``__UserSettings`` (one row per
  ``(user, doctype)``, a JSON ``data`` column), under the keys ``Project Planner`` and
  ``Maintenance Planner`` and inside one top-level key of the row, ``planner_views``. Frappe's
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

Every endpoint is gated by the calling planner's own roles (``project_planner._require_planner`` or
``maintenance_planner._require_planner``) and acts only on ``frappe.session.user``'s row.
"""

import json
import re

import frappe
from frappe import _

#: The planner a request is about -> its key in ``__UserSettings.doctype``.
PLANNERS = {"project": "Project Planner", "maintenance": "Maintenance Planner"}
#: The one top-level key of the row these endpoints own; anything else in the row is kept.
ROW_KEY = "planner_views"
STORE_VERSION = 1

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

#: What a view may hold, per planner: ``key -> ("bool",) | ("text",) | ("enum", values)``. The pages'
#: own constants are checked against this by tests/test_planner_phase6c.py.
SCHEMA = {
	"project": {
		"view": ("enum", ("week", "month", "crew", "heatmap")),
		"project": ("text",),
		"pm": ("text",),
		"group": ("enum", PROJECT_GROUPS),
		"foreign": ("bool",),
		"over_only": ("bool",),
		"panel": ("bool",),
		"color_by": ("enum", PROJECT_COLOR_MODES),
	},
	"maintenance": {
		"view": ("enum", ("month", "week", "crew")),
		"technician": ("text",),
		"projected": ("bool",),
		"project_work": ("bool",),
		"panel": ("bool",),
		"color_by": ("enum", MAINTENANCE_COLOR_MODES),
	},
}
#: Never remembered as "last used" (see the module docstring).
NOT_REMEMBERED = ("over_only",)

#: The print eyebrow and the print design system pillar of each planner.
PRINT_EYEBROW = {"project": "Project Planner", "maintenance": "Maintenance Planner"}
PRINT_PILLAR = {"project": None, "maintenance": "service"}

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


def _now_text():
	return str(frappe.utils.now_datetime())[:19]


# ---------------------------------------------------------------------- endpoints


@frappe.whitelist()
def get_views(planner):
	"""The caller's views for ``planner`` (``project`` | ``maintenance``).

	``{"planner", "last": {view} | None, "views": [{"name", "data": {view}, "saved_on"}], "max_views"}``
	"""
	planner, doctype = _require(planner)
	store = read_store(planner, _read_row(doctype))
	return {"planner": planner, "last": store["last"], "views": store["views"], "max_views": MAX_VIEWS}


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
