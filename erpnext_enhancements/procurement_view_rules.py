"""The Procurement Tracker's saved views: what a layout may hold, and who may touch one (v1.816.0).

Pure functions, no ``frappe`` import, so ``tests/test_procurement_view_rules.py`` runs them in CI
without a bench. ``procurement_views.py`` is the thin layer that reads and writes the records; every
decision lives here.

**A view** is a named layout for one of the tracker's two groupings (``documents`` or ``items``)::

        {
            "columns": ["item", "where", ...],  # shown, in this order
            "sort": {"key": "status", "dir": "asc"},  # or None
            "filters": {
                "location": "",
                "status": "",
                "supplier": "",
                "action": False,
                "noprice": False,
                "rules": [{"column": "received", "op": "between", "min": 1, "max": None}, ...],
            },
        }

The columns each grouping has, the operators a filter on each kind of column may use, the limits
and the views that ship with the app are all in ``data/procurement_tracker_views.json``, the one file
the browser's column registry is also held to (``scripts/test_procurement_tracker.mjs``).

**What a layout can never do.** It is a list of names. It carries no data and no query, so a
view cannot make the tracker show a column the person's feed did not send: the price columns are
absent from the response for anybody who may not read Purchase Orders
(``project_enhancements._price_access``), and a view that names them simply has nothing to draw.
:func:`validate_layout` is strict (known column keys only, size limits, an operator that suits the
column) and is what a save goes through. :func:`repair_layout` is lenient and never raises: a stored
view that is outdated (a column the app no longer has) loses that column, and one that is garbage
becomes the standard layout, so a broken record cannot break the tracker.

**Who may touch a view.** Its owner, and nobody else, on every read, change and delete
(:func:`check_owner`). The presets are not records at all: they are in the JSON file and cannot be
changed from here, only copied.

Indentation is tabs (new file).
"""

import copy
import json
import math
import os
import re
from datetime import date

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "procurement_tracker_views.json")

with open(_DATA, encoding="utf-8") as _fh:
	CATALOG = json.load(_fh)

LIMITS = CATALOG["limits"]
MODES = tuple(CATALOG["modes"])
OPERATORS = CATALOG["operators"]

#: The filters a view stores besides its rules, and what each is when it is off.
FILTER_DEFAULTS = {"location": "", "status": "", "supplier": "", "action": False, "noprice": False}
_STRING_FILTERS = ("location", "status", "supplier")
_BOOL_FILTERS = ("action", "noprice")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ViewError(ValueError):
	"""A view the server will not store, or a request it will not honour. The message is for the
	person (purchasing and warehouse staff), so it is plain words."""


class NotYours(ViewError):
	"""Someone else's view. Reported as 'not found' by the endpoint: it does not confirm that a
	record exists."""


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


def columns(mode):
	"""The columns of one grouping, in the order the picker lists them."""
	if mode not in MODES:
		raise ViewError("Choose By item or By document for this view.")
	return copy.deepcopy(CATALOG["modes"][mode]["columns"])


def column_map(mode):
	return {c["key"]: c for c in columns(mode)}


def presets():
	"""The views that ship with the app: ``[{"id", "name", "mode", "description", "layout"}]``."""
	return copy.deepcopy(CATALOG["presets"])


def preset(view_id):
	for entry in CATALOG["presets"]:
		if entry["id"] == view_id:
			return copy.deepcopy(entry)
	return None


def standard_layout(mode):
	"""The layout that is the tracker as it has always been, for one grouping."""
	entry = preset("std-" + mode) if mode in MODES else None
	if not entry:
		raise ViewError("Choose By item or By document for this view.")
	return entry["layout"]


def catalog_for_client():
	"""What the browser needs of the catalog: the columns, the operators, the limits, the presets."""
	return {
		"modes": copy.deepcopy(CATALOG["modes"]),
		"operators": copy.deepcopy(OPERATORS),
		"limits": copy.deepcopy(LIMITS),
		"presets": presets(),
	}


# ---------------------------------------------------------------------------
# Names, quota, owner
# ---------------------------------------------------------------------------


def clean_name(name):
	"""A view's name: one line, single spaces, 1 to ``max_name_length`` characters."""
	text = re.sub(r"\s+", " ", str(name if name is not None else "")).strip()
	if not text:
		raise ViewError("Give the view a name.")
	if _CONTROL.search(text):
		raise ViewError("A view's name cannot hold control characters.")
	if len(text) > LIMITS["max_name_length"]:
		raise ViewError(f"A view's name can be at most {LIMITS['max_name_length']} characters.")
	return text


def name_key(name):
	"""What two names are compared by: case and spacing do not make a different name."""
	return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def check_unique_name(name, others):
	"""``others`` are the names of the person's other views (not the one being saved)."""
	taken = {name_key(n) for n in others or ()}
	if name_key(name) in taken:
		raise ViewError(f'You already have a view called "{name}". Pick another name.')


def check_quota(count):
	"""``count`` is how many views the person already has, before this one."""
	if count >= LIMITS["max_views_per_user"]:
		raise ViewError(
			f"You can keep up to {LIMITS['max_views_per_user']} views. Delete one you no longer use first."
		)


def check_owner(owner, user):
	"""Only a view's owner may read, change or delete it. Raises :class:`NotYours` otherwise."""
	if not owner or not user or owner != user:
		raise NotYours("That view was not found.")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _text(value, limit, what):
	if not isinstance(value, str):
		raise ViewError(f"{what} must be text.")
	text = value.strip()
	if _CONTROL.search(text):
		raise ViewError(f"{what} cannot hold control characters.")
	if len(text) > limit:
		raise ViewError(f"{what} is too long.")
	return text


def _number(value, what):
	if value is None or value == "":
		return None
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise ViewError(f"{what} must be a number.")
	try:
		in_range = math.isfinite(value) and abs(value) <= 1e12
	except OverflowError:
		# An integer too long to be a float (10 ** 400): out of range, not a crash.
		in_range = False
	if not in_range:
		raise ViewError(f"{what} is out of range.")
	return value


def _date(value, what):
	if value is None or value == "":
		return None
	if not isinstance(value, str):
		raise ViewError(f"{what} must be a date.")
	try:
		return date.fromisoformat(value).isoformat()
	except ValueError:
		raise ViewError(f"{what} must be a date.") from None


def _rule(raw, cmap):
	if not isinstance(raw, dict):
		raise ViewError("A filter must be a column, a test and a value.")
	label = "A filter"
	column = raw.get("column")
	if not isinstance(column, str) or column not in cmap:
		raise ViewError("A filter names a column this grouping does not have.")
	kind = cmap[column].get("filter")
	label = f"The {cmap[column]['label']} filter"
	if not kind:
		raise ViewError(f"{label} is not available: that column cannot be filtered.")
	op = raw.get("op")
	if op not in OPERATORS[kind]:
		raise ViewError(f"{label} uses a test that does not suit this column.")
	out = {"column": column, "op": op}
	if op == "contains":
		value = _text(raw.get("value"), LIMITS["max_text_length"], f"{label}'s text")
		if not value:
			raise ViewError(f"{label} needs some text to look for.")
		out["value"] = value
	elif op == "any_of":
		values = raw.get("values")
		if not isinstance(values, list) or not values:
			raise ViewError(f"{label} needs at least one choice.")
		if len(values) > LIMITS["max_choices"]:
			raise ViewError(f"{label} has too many choices.")
		seen = []
		for value in values:
			text = _text(value, LIMITS["max_choice_length"], f"A choice in {label}")
			if not text:
				raise ViewError(f"{label} has an empty choice.")
			if text not in seen:
				seen.append(text)
		out["values"] = seen
	elif op == "between":
		read = _number if kind == "number" else _date
		low = read(raw.get("min"), f"The lower end of {label}")
		high = read(raw.get("max"), f"The upper end of {label}")
		if low is None and high is None:
			raise ViewError(f"{label} needs a lower end, an upper end, or both.")
		if low is not None and high is not None and low > high:
			raise ViewError(f"The lower end of {label} is above its upper end.")
		out["min"] = low
		out["max"] = high
	return out


def _filters(raw, cmap):
	if raw is None:
		raw = {}
	if not isinstance(raw, dict):
		raise ViewError("The view's filters are not in a form the tracker can read.")
	out = {}
	for key in _STRING_FILTERS:
		value = raw.get(key, "")
		out[key] = "" if value is None else _text(value, LIMITS["max_choice_length"], f"The {key} filter")
	for key in _BOOL_FILTERS:
		value = raw.get(key, False)
		if value not in (True, False, 0, 1, None):
			raise ViewError(f"The {key} filter must be on or off.")
		out[key] = bool(value)
	rules = raw.get("rules")
	if rules is None:
		rules = []
	if not isinstance(rules, list):
		raise ViewError("The view's filters are not in a form the tracker can read.")
	if len(rules) > LIMITS["max_rules"]:
		raise ViewError(f"A view can have up to {LIMITS['max_rules']} column filters.")
	out["rules"] = [_rule(r, cmap) for r in rules]
	return out


def validate_layout(layout, mode):
	"""The layout in its stored form, or a :class:`ViewError` saying what is wrong with it.

	Only the three known keys survive; anything else sent along is dropped. Column keys must be
	the grouping's own and appear once; the sort must be on a shown, sortable column; the whole
	thing must fit ``max_layout_bytes`` once written."""
	cmap = column_map(mode)
	if isinstance(layout, str):
		try:
			layout = json.loads(layout)
		except ValueError:
			raise ViewError("The view's layout could not be read.") from None
	if not isinstance(layout, dict):
		raise ViewError("The view's layout could not be read.")
	cols = layout.get("columns")
	if not isinstance(cols, list) or not cols:
		raise ViewError("Show at least one column.")
	shown = []
	for key in cols:
		if not isinstance(key, str) or key not in cmap:
			raise ViewError("The view names a column this grouping does not have.")
		if key in shown:
			raise ViewError("A column is listed twice.")
		shown.append(key)
	sort = layout.get("sort")
	if sort is not None:
		if not isinstance(sort, dict) or sort.get("dir") not in ("asc", "desc"):
			raise ViewError("The view's sort could not be read.")
		key = sort.get("key")
		if key not in shown or cmap[key].get("sortable") is False:
			raise ViewError("The view sorts on a column that is not shown or cannot be sorted.")
		sort = {"key": key, "dir": sort["dir"]}
	out = {"columns": shown, "sort": sort, "filters": _filters(layout.get("filters"), cmap)}
	if len(layout_json(out).encode("utf-8")) > LIMITS["max_layout_bytes"]:
		raise ViewError("This view has too much in it. Remove some filters or columns.")
	return out


def layout_json(layout):
	"""The layout as it is stored: compact, keys sorted, so equal layouts are equal text."""
	return json.dumps(layout, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def repair_layout(raw, mode):
	"""``(layout, repaired)``: a layout the tracker can draw from whatever is stored.

	Never raises. A valid layout comes back unchanged. A stored one that is outdated or damaged
	is mended as far as it can be (unknown columns dropped, a filter that no longer suits its
	column dropped, a sort on a missing column dropped) and, if nothing usable is left, is the
	standard layout. ``repaired`` says it was not used as stored, so the tracker can tell the person."""
	try:
		return validate_layout(raw, mode), False
	except ViewError:
		pass
	standard = standard_layout(mode)
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
		if not isinstance(data, dict):
			return standard, True
		cmap = column_map(mode)
		cols, seen = [], set()
		for key in data.get("columns") if isinstance(data.get("columns"), list) else []:
			if isinstance(key, str) and key in cmap and key not in seen:
				seen.add(key)
				cols.append(key)
		if not cols:
			return standard, True
		sort = data.get("sort")
		if not (
			isinstance(sort, dict)
			and sort.get("dir") in ("asc", "desc")
			and sort.get("key") in seen
			and cmap[sort["key"]].get("sortable") is not False
		):
			sort = None
		else:
			sort = {"key": sort["key"], "dir": sort["dir"]}
		filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
		rules = []
		for rule in filters.get("rules") if isinstance(filters.get("rules"), list) else []:
			try:
				rules.append(_rule(rule, cmap))
			except ViewError:
				continue
		rules = rules[: LIMITS["max_rules"]]
		basics = {}
		for key in _STRING_FILTERS:
			try:
				basics[key] = _filters({key: filters.get(key, "")}, cmap)[key]
			except ViewError:
				basics[key] = ""
		for key in _BOOL_FILTERS:
			basics[key] = filters.get(key) is True
		basics["rules"] = rules
		return validate_layout({"columns": cols, "sort": sort, "filters": basics}, mode), True
	except ViewError:
		return standard, True
	except Exception:
		return standard, True
