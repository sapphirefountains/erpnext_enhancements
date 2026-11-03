# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Seed the Fountain Designer catalog from digitized manufacturer data, approved rows only.

Nikolas decided on 2026-11-01 that the designer's catalog comes from public manufacturer
data, cited per row and approved by him before it reaches the site. The data lives in the
repo, in ``docs/fountain-designer/catalog/``:

* ``pumps.json``, ``nozzles.json``, ``lights.json`` — the digitized rows, each with its source.
* ``purchasing.json`` — where each row can be bought and for how much, by row id (optional).
* ``approved.json`` — the rows Nikolas approved, by ``"Manufacturer|Model"`` key::

	{
	  "approved_by": "nikolas.bradshaw@sapphirefountains.com",
	  "approved_on": "2026-11-05",
	  "pumps":   ["pentair-eq-eq500", "Atlantic|TT1500", {"key": "Atlantic|A-21", "item_code": "500141"}],
	  "nozzles": ["OASE|Geyser 35-10 E"],
	  "lights":  []
	}

  A plain string approves the row as it is: ``"Manufacturer|Model"``, or the row's ``id`` (no
  ``|``). An object (``key`` or ``id``) may also map the row onto an Item that already exists
  (``item_code``) or choose the new Item's ``item_name`` / ``item_code``. Rows that share a
  manufacturer + model are told apart by part number and must be approved by ``id``.

Run it from a shell, dry run first (nothing written; the summary says what would change)::

	bench --site <site> execute erpnext_enhancements.water_engineering.catalog_seed.load_catalog
	bench --site <site> execute erpnext_enhancements.water_engineering.catalog_seed.load_catalog \\
	    --kwargs "{'dry_run': 0}"

or as a System Manager over HTTP: ``seed_catalog`` (POST, ``dry_run`` defaults to 1).

What it is careful about:

* **Only approved rows, and never on migrate.** A row whose key is not in ``approved.json`` is
  reported and skipped. Nothing here is wired to ``after_migrate``: the catalog reaches a site
  when a person runs this, not when a deploy happens.
* **The Item code is the manufacturer's part number** (Nikolas, 2026-11-02; :func:`item_code_for`):
  the row's ``part_no`` without its trailing note (``340030 (EQ500 1-ph)`` -> ``340030``), else,
  when there is none, it names a range, or another row holds it too, the model as written
  (``NBS 015-090``). An ``approved.json`` ``item_code`` always wins. Codes are judged across all
  three files at once (:func:`assign_codes`) and compared as the Item naming guard compares them;
  a collision, with another row or with an Item already on the site, refuses the row with the
  reason. Nothing is ever suffixed to make a code unique. The summary's ``codes`` shows each
  row's code. Items loaded before this rule are renamed by :func:`rename_catalog_items`.
* **Merges.** Sometimes ``approved.json`` points a row at an Item Sapphire already had, while the
  loader's own Item for that row is still on the site. :func:`rename_catalog_items` then merges
  them instead of renaming:
  - it writes the catalog's own values for the row (never the old Item's) onto the existing Item;
  - it moves the curves there;
  - it deletes the loader's Item, and refuses to if anything else links to it;
  - until that has run, a load refuses the row.
  This is the one place anything here deletes an Item.
* **Idempotent, keyed on the catalog row id.** The Item is found by the approval's
  ``item_code``, else by the ``custom_catalog_id`` the loader stamped on it (so a renamed Item is
  found under its new name), else by ``brand`` + ``custom_model_no``, else created. A Pump Curve
  is found by Item + curve label, a Nozzle Profile by catalog id, else manufacturer + model. A
  second run with the same files changes nothing and reports every row ``unchanged``.
* **It writes specs and provenance, never a person's curation.** On an Item that already exists
  it sets the spec and provenance fields only; the name, group, description, brand and stock
  settings are filled when blank and otherwise left alone. It never deletes a curve, a profile
  row it did not write, or an Item.
* **Provenance on every record**: source URL, document and page, ``verified_by`` (the approver,
  when that is a User) and ``verified_on`` (the approval date).
* **One bad row does not stop the rest.** Each row is checked before anything is written (the
  same curve checks the Pump Curve form applies) and written in its own savepoint; a failure
  is reported in ``errors`` with the row's key.
* **A dry run refuses the rows a write would.** Frappe checks a value against its column's
  length only on insert and save, so a dry run that skips those never saw it: on 2026-11-02 a
  clean dry run was followed by a write that refused 120 of 380 rows for a revision or URL
  over Data's 140 characters. Every value about to be written, child rows included, is now
  checked against the target field's type and ``length`` (``_check_lengths``, Frappe's own
  ``_validate_length`` rule) in both modes, and an overlong one is the row's error. Nothing
  is truncated.
* **Where to buy it** (v1.776.0): ``purchasing.json`` gives a row its seller, the seller's part number,
  a link and an advertised price. The loader makes the seller a Supplier (exact name, else created;
  never edited, and a name differing only by case or spacing from an existing one is refused), sets
  that supplier's Item Supplier row and leaves every other row alone, and keeps one Item Price per
  seller on the file's buying list, replaced only by a later sighting (:func:`apply_purchasing`).
* **The Item naming guard is bypassed on purpose** (``flags.ignore_naming_guard``), as for the
  other in-request callers whose user cannot choose the code: the code is generated here, or
  set in ``approved.json``.
"""

import datetime
import json
import re
import time
from pathlib import Path

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming_rules
from erpnext_enhancements.water_engineering import catalog_reader
from erpnext_enhancements.water_engineering.doctype.nozzle_profile.nozzle_profile import (
	check_performance_points,
)
from erpnext_enhancements.water_engineering.doctype.pump_curve.pump_curve import check_points
from erpnext_enhancements.water_engineering.engine.constants import FT_PER_PSI

CATALOG_DIR = Path(__file__).resolve().parents[2] / "docs" / "fountain-designer" / "catalog"
FILES = {"pumps": "pumps.json", "nozzles": "nozzles.json", "lights": "lights.json"}
APPROVAL_FILE = "approved.json"
PURCHASING_FILE = "purchasing.json"
KW_PER_HP = 0.745699872
#: ``Item.item_code`` is a Data field: Frappe's 140 characters.
ITEM_CODE_LENGTH = 140
#: Words that make a ``part_no`` a range of part numbers rather than one (:func:`part_number`).
RANGE_WORDS = re.compile(r"\.\.\.|\u2026|\bto\b|x{3}", re.IGNORECASE)

#: Item Groups the new Items go in: (group, parent group). "Pumps" already exists on prod.
GROUPS = {
	"pumps": ("Pumps", "All Item Groups"),
	"nozzles": ("Nozzles", "Aquatic Equipment"),
	"lights": ("Lights", "Aquatic Equipment"),
	"aerators": ("Aerators", "Aquatic Equipment"),
}
#: ``confidence`` in the data -> Pump Curve ``source``.
CONFIDENCE_SOURCE = {
	"table": "Datasheet",
	"datasheet": "Datasheet",
	"digitized": "Digitized",
	"rep": "Rep-supplied",
	"rep-supplied": "Rep-supplied",
	"generic": "Generic estimate",
	"generic estimate": "Generic estimate",
}
#: Words in the data's nozzle ``type`` -> Nozzle Profile ``nozzle_type``. First match wins.
NOZZLE_TYPE_WORDS = [
	("foam", "Foam / Aerating"),
	("aerat", "Foam / Aerating"),
	("laminar", "Laminar"),
	("geyser", "Geyser"),
	("bubbler", "Bubbler"),
	("ring", "Spray Ring"),
	("screen", "Water Screen"),
	("curtain", "Water Screen"),
	("sheet", "Sheet Fall"),
	("cascade", "Cascade / Fan"),
	("fan", "Cascade / Fan"),
	("tulip", "Cascade / Fan"),
	("mushroom", "Cascade / Fan"),
	("spray", "Spray Ring"),
	("jet", "Orifice Jet"),
	("orifice", "Orifice Jet"),
	("smooth", "Orifice Jet"),
	("comet", "Orifice Jet"),
]
#: The research data's own ``type`` vocabulary, matched before the word search.
NOZZLE_TYPES_EXACT = {
	"orifice jet": "Orifice Jet",
	"foam/aerating": "Foam / Aerating",
	"laminar": "Laminar",
	"cascade/fan": "Cascade / Fan",
	"geyser": "Geyser",
	"bubbler": "Bubbler",
	"spray ring": "Spray Ring",
	"water screen": "Water Screen",
	"sheet fall": "Sheet Fall",
	"other": "Other",
}
#: Types whose display moves with the water depth over the nozzle.
LEVEL_SENSITIVE_TYPES = {"Foam / Aerating", "Geyser"}
#: A printed length in inches: an optional whole part, then a fraction or a decimal, then an
#: optional unit (``4"``, ``3-3/4"``, ``3 3/4 in``, ``4.25``).
INCHES = r'\s*(?:(\d+(?:\.\d+)?)[\s-]+)?(?:(\d+)/(\d+)|(\d+(?:\.\d+)?))\s*(?:"|in\.?|inch(?:es)?)?\s*'
LIGHT_CONTROL = {"switched": "Switched", "dmx": "DMX", "proprietary": "Proprietary"}


class CatalogRowError(Exception):
	"""A data row that cannot be loaded; the message names what is wrong."""


# ----------------------------------------------------------------------------- reading


def catalog_key(manufacturer, model):
	"""``"manufacturer|model"``, case- and spacing-insensitive: the approval and lookup key."""

	def norm(value):
		return " ".join(str(value or "").split()).casefold()

	return f"{norm(manufacturer)}|{norm(model)}"


def _read_json(path):
	path = Path(path)
	if not path.exists():
		return None
	return json.loads(path.read_text(encoding="utf-8"))


def rows_of(data, kind):
	"""The row list in a data file: a bare list, or ``{kind: [...]}`` / ``{"rows": [...]}``."""
	if data is None:
		return []
	if isinstance(data, list):
		return data
	for key in (kind, "rows", "items"):
		if isinstance(data.get(key), list):
			return data[key]
	return []


def read_approvals(data):
	"""``(meta, {kind: {"keys": {key: overrides}, "ids": {id: overrides}}})`` from ``approved.json``.

	An entry is a ``"Manufacturer|Model"`` string, a row ``id`` string (no ``|``), or an object
	with ``key`` or ``id`` plus overrides (``item_code``, ``item_name``).
	"""
	data = data or {}
	meta = {"approved_by": data.get("approved_by") or "", "approved_on": data.get("approved_on") or ""}
	out = {}
	for kind in FILES:
		keys, ids = {}, {}
		for entry in data.get(kind) or []:
			overrides = {}
			if isinstance(entry, dict):
				overrides = {k: v for k, v in entry.items() if k not in ("key", "id")}
				ref = entry.get("key") or ("" if not entry.get("id") else None)
				if ref is None:
					ids[str(entry["id"]).strip()] = overrides
					continue
				entry = ref
			if not isinstance(entry, str) or not entry.strip():
				continue
			if "|" in entry:
				manufacturer, _sep, model = entry.partition("|")
				keys[catalog_key(manufacturer, model)] = overrides
			else:
				ids[entry.strip()] = overrides
		out[kind] = {"keys": keys, "ids": ids}
	return meta, out


def resolve_rows(rows):
	"""``[(key, base_key, row)]``: each row with its identity key.

	The key is manufacturer + model, except where several rows of a file share it (the same
	model sold as separate part numbers, e.g. a 1-phase and a 3-phase SKU): then each such row
	is told apart by its first part number, which becomes part of its model, so the rows reach
	separate Items instead of overwriting one another on every run.
	"""
	counts = {}
	for row in rows:
		base = catalog_key(row.get("manufacturer"), row.get("model"))
		counts[base] = counts.get(base, 0) + 1
	out = []
	for row in rows:
		base = catalog_key(row.get("manufacturer"), row.get("model"))
		if counts[base] > 1:
			parts = row.get("part_no")
			part = (parts[0] if isinstance(parts, list) and parts else parts) or row.get("id") or ""
			part = str(part).split(" (")[0].strip()
			row = {**row, "model": f"{str(row.get('model') or '').strip()} {part}".strip()}
		out.append((catalog_key(row.get("manufacturer"), row.get("model")), base, row))
	return out


# ----------------------------------------------------------------------------- planning
# Pure: a data row + its approval -> the field values to write. No database access, so the
# bench-free suite checks every mapping directly.


def _pick(row, *names):
	for name in names:
		if row.get(name) not in (None, ""):
			return row[name]
	return None


def _num(value):
	try:
		return float(value) if value not in (None, "") else None
	except (TypeError, ValueError):
		return None


def _required(row, name):
	value = row.get(name)
	if value in (None, "") or not str(value).strip():
		raise CatalogRowError(_("{0} is missing.").format(name))
	return str(value).strip()


def _slug(value):
	return re.sub(r"[^A-Z0-9]+", "-", str(value).upper()).strip("-")


def _as_list(value):
	if value in (None, ""):
		return []
	if isinstance(value, (list, tuple)):
		return [v for v in value if v not in (None, "")]
	return [part.strip() for part in re.split(r"[/,]", str(value)) if part.strip()]


def _fmt(value):
	number = _num(value)
	return f"{number:g}" if number is not None else str(value)


def _text(value):
	"""A list (``listing``, ``part_no``) joined with "; ", a scalar as text, blank as None."""
	if isinstance(value, (list, tuple)):
		value = "; ".join(str(v).strip() for v in value if v not in (None, ""))
	value = str(value).strip() if value not in (None, "") else ""
	return value or None


def provenance(row, meta):
	"""Where a row's numbers came from and how they were read, for every record it writes.

	``{id, url, document, page, also, confidence, method, error_band_pct, generic, approved_by,
	approved_on}``. ``source`` is a list of ``{url, title, page, accessed}`` in the research data
	(the first entry is the one the numbers were read from; the rest are listed in ``also``), or
	a single such object, or a bare URL.
	"""
	sources = row.get("source") or {}
	sources = [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else [sources]
	source = sources[0] if sources else {}
	if isinstance(source, str):
		source = {"url": source}
	also = [
		" ".join(str(s.get(k) or "").strip() for k in ("title", "url")).strip()
		for s in sources[1:]
		if isinstance(s, dict)
	]
	document = str(source.get("document") or source.get("title") or "").strip()
	if source.get("accessed"):
		document = f"{document} (accessed {source['accessed']})".strip()
	confidence = str(row.get("confidence") or "").strip().casefold()
	return {
		"id": str(row.get("id") or "").strip(),
		"url": str(source.get("url") or "").strip(),
		"document": document,
		"page": str(source.get("page") or "").strip(),
		"also": [a for a in also if a],
		"confidence": confidence,
		"method": str(row.get("method") or "").strip(),
		"error_band_pct": _num(row.get("error_band_pct")),
		"generic": confidence.startswith("generic"),
		"approved_by": meta.get("approved_by") or "",
		"approved_on": str(meta.get("approved_on") or "")[:10],
	}


def _item_provenance(prov):
	revision = prov["document"]
	if prov["page"]:
		revision = f"{revision}, p. {prov['page']}".strip(", ")
	return {
		"custom_spec_source_url": prov["url"] or None,
		"custom_spec_revision": revision or None,
		"custom_generic_estimate": 1 if prov["generic"] else 0,
		"custom_spec_verified_by": prov["approved_by"] or None,
		"custom_spec_verified_on": prov["approved_on"] or None,
		"custom_catalog_id": prov["id"] or None,
		"custom_spec_confidence": prov["confidence"] or None,
		"custom_spec_method": prov["method"] or None,
		"custom_spec_error_band_pct": prov["error_band_pct"],
	}


def part_number(value):
	"""The one manufacturer part number a row's ``part_no`` names, or None when it names no single part.

	The first entry of a list (``["340030 (EQ500 1-ph)", "340031 ..."]``), with every trailing
	parenthetical note removed and whitespace trimmed, and the first of a slash-separated set of
	alternatives (``"F2462038 / F2462051 (ball joint)"``, ``"IWC15W100 / IWC15W200"``). A range is
	not a part: ``"7.3JF100 to 7.3JF400 (cord length variants)"``, ``"LED3125050 ... LED3125400"``,
	``"601000-601004"`` (two equal-length numbers, the second larger).
	"""
	if isinstance(value, (list, tuple)):
		value = value[0] if value else None
	text = " ".join(str(value if value is not None else "").split())
	while True:
		trimmed = re.sub(r"\s*\([^()]*\)$", "", text)
		if trimmed == text:
			break
		text = trimmed
	text = text.split(" / ")[0].strip()
	if not text or RANGE_WORDS.search(text):
		return None
	numbers = re.fullmatch(r"(\d+)-(\d+)", text)
	if numbers and len(numbers.group(1)) == len(numbers.group(2)) and int(numbers.group(2)) > int(numbers.group(1)):
		return None
	return text


def model_code(model):
	"""A model designation as an Item code: as written, whitespace collapsed, commas dropped.

	The comma goes because the Item naming rules ask for exactly that of a code (SOP §6, "strip the
	comma only, keep every other character"): ``3656LH 2x2-5 (also 3756LH), 3450 rpm, trim A 4"``.
	"""
	return " ".join(str(model or "").replace(",", " ").split())


def item_code_for(row, approval=None, shared=()):
	"""``(code, basis)`` for one row: the rule every catalog Item code follows (Nikolas, 2026-11-02).

	* an ``approved.json`` entry's ``item_code`` always wins (that is how a row is attached to a
	  Sapphire Item that already exists);
	* else the manufacturer's part number (:func:`part_number`);
	* else, when the row has none, names a range, or shares it with another row, the model
	  designation (:func:`model_code`). ``shared`` is ``{normalized part number: [part numbers]}``
	  for the part numbers more than one row holds, compared as the Item naming guard compares
	  codes: ``SHPF.50`` (a 0.5 HP pump) and ``SHPF5.0`` (a 5 HP one) are one code to the guard, so
	  neither row may have it.

	No suffix is ever added to make a code unique: :func:`assign_codes` refuses a collision instead.
	"""
	if (approval or {}).get("item_code"):
		return str(approval["item_code"]).strip(), "approved.json item_code"
	part = part_number(row.get("part_no"))
	model = model_code(row.get("model"))
	holders = (shared or {}).get(naming_rules.normalise(part)) if part else None
	if part and not holders:
		return part, "part_no"
	if part:
		others = sorted({h for h in holders if h.casefold() != part.casefold()})
		if others:
			return model, f"model (part no. {part} is {', '.join(others)} once case and punctuation are ignored)"
		return model, f"model (part no. {part} is shared with another row)"
	if row.get("part_no") not in (None, "", []):
		return model, "model (part_no is a range, not one part)"
	return model, "model (no part_no)"


def assign_codes(entries):
	"""``{(kind, label): {code, basis} | {error}}`` for every row of every file at once.

	``entries`` is ``[(kind, label, row, approval_or_None)]``. Codes are judged across all three
	kinds together, approved or not, so a row's code does not change when another row is approved:
	a part number held by two rows falls back to the model on both, and a code that still
	collides with another row's (compared as the Item naming guard compares codes, ignoring case
	and punctuation) is an error on every row that did not choose it in ``approved.json``.
	"""
	parts = {}
	for _kind, _label, row, _approval in entries:
		part = part_number(row.get("part_no"))
		if part:
			parts.setdefault(naming_rules.normalise(part), []).append(part)
	shared = {norm: held for norm, held in parts.items() if len(held) > 1}
	out, holders = {}, {}
	for kind, label, row, approval in entries:
		code, basis = item_code_for(row, approval, shared)
		if not code:
			out[(kind, label)] = {"error": _("The row has neither a part number nor a model to name its Item.")}
			continue
		if len(code) > ITEM_CODE_LENGTH:
			out[(kind, label)] = {"error": f"Item code {code!r} is longer than {ITEM_CODE_LENGTH} characters."}
			continue
		out[(kind, label)] = {"code": code, "basis": basis, "chosen": bool((approval or {}).get("item_code"))}
		holders.setdefault(naming_rules.normalise(code), []).append((kind, label))
	for keys in holders.values():
		if len(keys) < 2:
			continue
		for key in keys:
			if out[key]["chosen"]:
				continue
			others = ", ".join(label for kind, label in keys if (kind, label) != key)
			out[key] = {
				"error": f"Item code {out[key]['code']!r} is also the code of {others}; "
				"set item_code for one of them in approved.json"
			}
	return out


def _item_plan(kind, row, approval, name_word, specs, description, prov, code=None):
	"""The Item half of a plan: fields always written, and fields filled only when blank.

	``code`` is the row's code from :func:`assign_codes`; without it (one row planned on its own)
	the row is coded by :func:`item_code_for` alone. ``legacy_code`` is the code the loader gave
	before 2026-11-02 (``PUMP-PENTAIR-EQ500-EQK500``), which :func:`rename_catalog_items` renames.
	"""
	manufacturer, model = _required(row, "manufacturer"), _required(row, "model")
	code = item_code_for(row, approval)[0] if (approval.get("item_code") or not code) else code
	return {
		"item_code": code,
		"legacy_code": f"{name_word}-{_slug(manufacturer)}-{_slug(model)}",
		"match_existing_code": bool(approval.get("item_code")),
		"catalog_id": prov["id"],
		"manufacturer": manufacturer,
		"model": model,
		"set": {**specs, **_item_provenance(prov)},
		"fill": {
			"item_name": approval.get("item_name") or f"{name_word}, {manufacturer} {model}".upper(),
			"item_group": GROUPS[kind][0],
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"brand": manufacturer,
			"description": description,
		},
	}


def _inches(value):
	"""Inches from a number or printed text: ``4``, ``'4"'``, ``'3-3/4"'``, ``'3/4 in'``, ``'4.25'``."""
	number = _num(value)
	if number is not None or value in (None, ""):
		return number
	match = re.fullmatch(INCHES, str(value))
	if not match:
		return None
	whole, num, den, plain = match.groups()
	if plain is not None:
		return float(plain) if whole is None else None  # "3 4.5" is not a measurement
	return float(whole or 0) + float(num) / float(den)


def _bhp(point):
	"""Brake horsepower of a curve point: given directly, or converted from ``kw``."""
	bhp = _num(_pick(point, "power_bhp", "bhp"))
	if bhp is not None:
		return bhp
	kw = _num(point.get("kw"))
	return round(kw / KW_PER_HP, 3) if kw is not None else None


def plan_pump(row, approval, meta, code=None):
	"""``{key, item, curves, warnings}`` for one ``pumps.json`` row. Raises CatalogRowError."""
	manufacturer, model = _required(row, "manufacturer"), _required(row, "model")
	prov = provenance(row, meta)
	voltages = [_fmt(v) for v in _as_list(_pick(row, "voltage", "voltages"))]
	fla = _pick(row, "fla_amps", "fla")
	if isinstance(fla, dict):
		# Keys are a voltage or "voltage phase" ("230 1-ph"); values as printed, so a range
		# stays a string. The Item's single FLA is the first value that is a number.
		fla_map = {str(k): v for k, v in fla.items() if v not in (None, "")}
		first_fla = next((_num(v) for v in fla_map.values() if _num(v) is not None), None)
	else:
		first_fla = _num(fla)
		fla_map = {voltages[0]: first_fla} if (voltages and first_fla is not None) else {}
	rpm = _pick(row, "rpm")
	variable = bool(row.get("variable_speed")) or str(rpm or "").strip().casefold().startswith("var")
	rpm = int(_num(rpm)) if _num(rpm) else None
	# A VS pump's main curve is drawn at curve_speed_rpm (its maximum speed).
	curve_rpm_main = int(_num(row["curve_speed_rpm"])) if _num(row.get("curve_speed_rpm")) else rpm
	source = CONFIDENCE_SOURCE.get(str(row.get("confidence") or "").strip().casefold(), "Digitized")
	if prov["generic"]:
		source = "Generic estimate"

	curve_rows = row.get("curves") or [
		{
			"points": _pick(row, "curve", "curve_points", "points") or [],
			"rpm": curve_rpm_main,
			"impeller_dia_in": _inches(_pick(row, "impeller_dia_in")),
		}
	]
	for extra in row.get("extra_curves") or []:
		extra_rpm = _num(extra.get("speed_rpm"))
		speed_pct = _num(extra.get("speed_pct"))
		if speed_pct is None and extra_rpm and curve_rpm_main:
			speed_pct = round(100.0 * extra_rpm / curve_rpm_main, 1)
		curve_rows.append(
			{
				"points": extra.get("curve") or extra.get("points") or [],
				"rpm": extra_rpm,
				"speed_pct": speed_pct,
				"impeller_dia_in": _inches(extra.get("impeller_dia_in")),
				"label": extra.get("label"),
			}
		)
	curves, warnings = [], []
	if row.get("alt_curves"):
		warnings.append("alt_curves (a second published curve that disagrees) not loaded")
	for spec in curve_rows:
		points = [
			{
				"flow_gpm": _num(_pick(p, "flow_gpm")),
				"head_ft": _num(_pick(p, "head_ft")),
				"efficiency_pct": _num(_pick(p, "eff_pct", "efficiency_pct")),
				"npshr_ft": _num(_pick(p, "npshr_ft")),
				"power_bhp": _bhp(p),
			}
			for p in spec.get("points") or []
		]
		ordered, errors, curve_warnings = check_points(points)
		if errors:
			raise CatalogRowError(" ".join(str(e) for e in dict.fromkeys(errors)))
		curve_rpm = int(_num(spec["rpm"])) if _num(spec.get("rpm")) else (None if variable else rpm)
		impeller = _inches(spec.get("impeller_dia_in"))
		speed = _num(spec.get("speed_pct")) or 100
		label = spec.get("label") or spec.get("curve_label")
		if not label:
			if impeller:
				label = f"{impeller:g} in impeller"
			elif curve_rpm:
				label = f"{curve_rpm} rpm"
			elif speed != 100:
				label = f"{speed:g} % speed"
			else:
				label = "Full speed" if variable else "Published curve"
		notes = [f"Seeded from docs/fountain-designer/catalog/pumps.json ({row.get('id') or model})."]
		if variable:
			notes.append("Variable-speed pump.")
		if prov["also"]:
			notes.append("Also cited: " + "; ".join(prov["also"]) + ".")
		if row.get("notes"):
			notes.append(str(row["notes"]))
		if prov["approved_by"]:
			notes.append(f"Approved by {prov['approved_by']} on {prov['approved_on']}.")
		curves.append(
			{
				"curve_label": str(label),
				"source": source,
				"impeller_dia_in": impeller,
				"rpm": curve_rpm,
				"speed_pct": speed,
				"max_flow_gpm": _num(spec.get("max_flow_gpm")) or ordered[-1]["flow_gpm"],
				"shutoff_head_ft": _num(spec.get("shutoff_head_ft"))
				or (ordered[0]["head_ft"] if ordered[0]["flow_gpm"] == 0 else None),
				"source_url": prov["url"] or None,
				"source_document": prov["document"] or None,
				"source_page": prov["page"] or None,
				"verified_by": prov["approved_by"] or None,
				"verified_on": prov["approved_on"] or None,
				"catalog_id": prov["id"] or None,
				"read_method": prov["method"] or None,
				"error_band_pct": prov["error_band_pct"],
				"notes": " ".join(notes),
				"points": ordered,
			}
		)
		warnings += [f"{label}: {w}" for w in curve_warnings]

	# The label is a curve's key on its Item, so two curves of one row must never share it.
	seen = {}
	for curve in curves:
		label = curve["curve_label"]
		seen[label] = seen.get(label, 0) + 1
		if seen[label] > 1:
			curve["curve_label"] = f"{label} ({seen[label]})"

	# The Item's ratings describe the curve the designs use: full speed, sourced, largest impeller.
	main = min(curves, key=catalog_reader.curve_rank)
	max_flow = _num(row.get("max_flow_gpm")) or main["max_flow_gpm"]
	shutoff = _num(row.get("shutoff_head_ft")) or main["shutoff_head_ft"] or main["points"][0]["head_ft"]
	# custom_pump_phase holds one phase; a row covering 1- and 3-phase variants leaves it blank
	# and lists both in the description (or map each part number to its own Item in approved.json).
	phases = [int(_num(p)) for p in _as_list(row.get("phase")) if _num(p) in (1.0, 3.0)]
	phase = str(phases[0]) if len(set(phases)) == 1 else None
	hp = _num(row.get("hp"))
	connections = _as_list(_pick(row, "connection_sizes", "connections", "connection_size"))
	if not connections:
		ports = [
			f"suction {_fmt(row['suction_in'])} in" if row.get("suction_in") else "",
			f"discharge {_fmt(row['discharge_in'])} in" if row.get("discharge_in") else "",
			str(row.get("connection_type") or ""),
		]
		connections = [p for p in ports if p]
	parts = [f"{manufacturer} {row.get('series') or ''} {model} pump".replace("  ", " ")]
	if hp:
		parts.append(f"{hp:g} HP")
	if _num(row.get("total_hp")):
		parts.append(f"{_fmt(row['total_hp'])} THP")
	if voltages:
		parts.append(f"{'/'.join(voltages)} V")
	if phases:
		parts.append("/".join(str(p) for p in dict.fromkeys(phases)) + "-phase")
	if fla_map:
		parts.append(
			"FLA "
			+ ", ".join(f"{_fmt(a)} A @ {v}{' V' if _num(v) is not None else ''}" for v, a in fla_map.items())
		)
	if connections:
		parts.append(f"connections {', '.join(str(c) for c in connections)}")
	if _text(row.get("part_no")):
		parts.append(f"part no. {_text(row['part_no'])}")
	specs = {
		"custom_equipment_class": "Pump",
		"custom_model_no": model,
		"custom_rated_gpm": max_flow,
		"custom_rated_tdh_ft": shutoff,
		"custom_pump_hp": hp,
		"custom_pump_phase": phase,
		"custom_pump_voltage": "/".join(voltages) or None,
		"custom_pump_fla_amps": first_fla,
		"custom_ul_listing": _text(_pick(row, "listing", "ul_listing")),
	}
	item = _item_plan("pumps", row, approval, "PUMP", specs, "; ".join(parts) + ".", prov, code)
	return {"key": catalog_key(manufacturer, model), "item": item, "curves": curves, "warnings": warnings}


def nozzle_type_for(text):
	"""Map the data's free-text nozzle type onto a Nozzle Profile ``nozzle_type`` option."""
	words = " ".join(str(text or "").casefold().split())
	if words in NOZZLE_TYPES_EXACT:
		return NOZZLE_TYPES_EXACT[words]
	for word, option in NOZZLE_TYPE_WORDS:
		if word in words:
			return option
	return "Other"


def plan_aerator(row, approval, meta, code=None):
	"""``{key, item, profile: None, warnings}`` for a floating aerator filed in ``nozzles.json``.

	The research data records Otterbine and Kasco floating aerators as one "nozzle" per unit
	and spray pattern, with a display height and spread but no pressure: they are bought as a
	unit, not fed by the designer's pump. So they become Aerator Items with the aerator fields,
	and no Nozzle Profile. HP and electrical data are printed in the row's notes only, which go
	into the description for the designer to read.
	"""
	manufacturer, model = _required(row, "manufacturer"), _required(row, "model")
	prov = provenance(row, meta)
	first = (_pick(row, "rows", "points") or [{}])[0]
	submergence = _num(row.get("min_submergence_in"))
	flow = _num(first.get("flow_gpm"))
	specs = {
		"custom_equipment_class": "Aerator",
		"custom_model_no": model,
		"custom_aerator_pattern_height_ft": _num(first.get("height_ft")),
		"custom_aerator_pattern_diameter_ft": _num(first.get("spread_ft")),
		"custom_aerator_min_depth_ft": round(submergence / 12.0, 2) if submergence else None,
		"custom_ul_listing": _text(row.get("listing")),
	}
	parts = [f"{manufacturer} {model} floating aerator"]
	if first.get("pattern"):
		parts.append(f"pattern {first['pattern']}")
	if flow:
		parts.append(f"{flow:g} GPM")
	if _text(row.get("part_no")):
		parts.append(f"part no. {_text(row['part_no'])}")
	description = "; ".join(parts) + "." + (f" {row['notes']}" if row.get("notes") else "")
	item = _item_plan("aerators", row, approval, "AERATOR", specs, description, prov, code)
	return {"key": catalog_key(manufacturer, model), "item": item, "profile": None, "warnings": []}


def plan_nozzle(row, approval, meta, code=None):
	"""``{key, item, profile, warnings}`` for one ``nozzles.json`` row. Raises CatalogRowError.

	A row none of whose performance rows has a pressure or head is a floating aerator and is
	handed to :func:`plan_aerator`.
	"""
	manufacturer, model = _required(row, "manufacturer"), _required(row, "model")
	table = _pick(row, "rows", "points", "performance") or []
	if table and all(_pick(r, "pressure_psi", "psi", "head_ft") is None for r in table):
		return plan_aerator(row, approval, meta, code)
	prov = provenance(row, meta)
	nozzle_type = nozzle_type_for(_pick(row, "type", "nozzle_type"))
	points = []
	for r in _pick(row, "rows", "points", "performance") or []:
		psi, head = _num(_pick(r, "pressure_psi", "psi")), _num(_pick(r, "head_ft"))
		points.append(
			{
				"psi": psi,
				"head_ft": head
				if head is not None
				else (round(psi * FT_PER_PSI, 3) if psi is not None else None),
				"flow_gpm": _num(_pick(r, "flow_gpm")),
				"display_height_ft": _num(_pick(r, "height_ft", "display_height_ft")),
				"display_diameter_ft": _num(_pick(r, "spread_ft", "diameter_ft", "display_diameter_ft")),
				"throw_ft": _num(_pick(r, "throw_ft")),
				"depth_over_nozzle_in": _num(_pick(r, "depth_over_nozzle_in", "depth_in")),
			}
		)
	if not points:
		raise CatalogRowError(_("A nozzle needs at least one performance row."))
	ordered, errors, warnings = check_performance_points(points)
	if errors:
		raise CatalogRowError(" ".join(str(e) for e in dict.fromkeys(errors)))
	mid = ordered[len(ordered) // 2]
	level_sensitive = row.get("level_sensitive")
	if level_sensitive is None:
		level_sensitive = nozzle_type in LEVEL_SENSITIVE_TYPES
	inlet = _pick(row, "inlet_size", "connection_size")
	notes = [f"Seeded from docs/fountain-designer/catalog/nozzles.json ({row.get('id') or model})."]
	if row.get("water_level_below_top_in") not in (None, ""):
		notes.append(f"Water level {_fmt(row['water_level_below_top_in'])} in below the nozzle top.")
	if row.get("notes"):
		notes.append(str(row["notes"]))
	profile = {
		"profile_name": f"{manufacturer} {model}",
		"manufacturer": manufacturer,
		"model_no": model,
		"nozzle_type": nozzle_type,
		"is_generic_estimate": 1 if prov["generic"] else 0,
		"connection_size": str(inlet) if inlet is not None else None,
		"level_sensitive": 1 if level_sensitive else 0,
		"min_depth_in": _num(_pick(row, "min_depth_in", "min_submergence_in")),
		"max_depth_in": _num(row.get("max_depth_in")),
		"recommended_beam_deg": _num(row.get("recommended_beam_deg")),
		"source_url": prov["url"] or None,
		"source_document": prov["document"] or None,
		"source_page": prov["page"] or None,
		"verified_by": prov["approved_by"] or None,
		"verified_on": prov["approved_on"] or None,
		"catalog_id": prov["id"] or None,
		"read_method": prov["method"] or None,
		"error_band_pct": prov["error_band_pct"],
		"performance": ordered,
	}
	# The rated-GPM method's anchor, from the middle row, and a published orifice / Cd: only
	# where the profile has none, so a designer's own values are never replaced.
	fill = {
		"notes": " ".join(notes),
		"rated_gpm": mid["flow_gpm"],
		"rated_head_ft": mid["head_ft"],
		"orifice_diameter_in": _num(row.get("orifice_in")),
		"discharge_coefficient": _num(row.get("discharge_coefficient")),
	}
	specs = {
		"custom_equipment_class": "Nozzle",
		"custom_model_no": model,
		"custom_ul_listing": _text(row.get("listing")),
	}
	description = (
		f"{manufacturer} {model} nozzle ({nozzle_type})"
		+ (f", {inlet} inlet" if inlet else "")
		+ (f", part no. {_text(row['part_no'])}" if _text(row.get("part_no")) else "")
		+ "."
	)
	item = _item_plan("nozzles", row, approval, "NOZZLE", specs, description, prov, code)
	return {
		"key": catalog_key(manufacturer, model),
		"item": item,
		"profile": profile,
		"profile_fill": fill,
		"warnings": warnings,
	}


def plan_light(row, approval, meta, code=None):
	"""``{key, item, warnings}`` for one ``lights.json`` row. Raises CatalogRowError."""
	manufacturer, model = _required(row, "manufacturer"), _required(row, "model")
	prov = provenance(row, meta)
	watts = _num(row.get("watts"))
	if watts is None or watts <= 0:
		raise CatalogRowError(_("A light needs its watts."))
	voltages = [_fmt(v) for v in _as_list(_pick(row, "voltage", "voltages"))]
	control = LIGHT_CONTROL.get(str(row.get("control") or "").strip().casefold())
	specs = {
		"custom_equipment_class": "Light",
		"custom_model_no": model,
		"custom_elec_watts": watts,
		"custom_elec_voltage": "/".join(voltages) or None,
		"custom_lumens": _num(row.get("lumens")),
		"custom_beam_deg": _num(_pick(row, "beam_deg", "beam")),
		"custom_light_control": control,
		"custom_max_depth_ft": _num(row.get("max_depth_ft")),
		"custom_ip_rating": _text(row.get("ip_rating")),
		"custom_ul_listing": _text(_pick(row, "listing", "ul_listing")),
	}
	volts = "/".join(voltages)
	if volts and all(_num(v) is not None for v in voltages):
		volts += " V"
	description = f"{manufacturer} {model} underwater light, {watts:g} W" + (f", {volts}." if volts else ".")
	item = _item_plan("lights", row, approval, "LIGHT", specs, description, prov, code)
	return {"key": catalog_key(manufacturer, model), "item": item, "warnings": []}


PLANNERS = {"pumps": plan_pump, "nozzles": plan_nozzle, "lights": plan_light}


# ----------------------------------------------------------------------------- writing


def _same(current, desired):
	"""True when the stored ``current`` already holds ``desired`` (numbers within 1e-6, dates as ISO)."""
	if desired in (None, ""):
		return current in (None, "")
	if isinstance(desired, (int, float)):
		number = _num(current)
		return number is not None and abs(number - float(desired)) < 1e-6
	if hasattr(current, "isoformat"):
		current = current.isoformat()
	return str(current if current is not None else "") == str(desired)


def _apply_fields(doc, values, fill_only=False):
	"""Set each differing value on ``doc``; with ``fill_only`` only where it is blank.

	A None value is skipped either way: the data being silent about a field is not a reason to
	clear what someone entered. Returns the changed field names.
	"""
	changed = []
	for field, value in values.items():
		if value is None:
			continue
		current = doc.get(field)
		if fill_only and current not in (None, "", 0):
			continue
		if not _same(current, value):
			doc.set(field, value)
			changed.append(field)
	return changed


def _rows_differ(current_rows, desired_rows, fields):
	"""True when a child table no longer holds ``desired_rows``.

	A value the data leaves blank matches a stored 0: child Float columns are NOT NULL DEFAULT
	0, so a point saved without NPSHr reads back 0, and treating that as a change would rewrite
	every such table on every run.
	"""
	if len(current_rows) != len(desired_rows):
		return True
	for cur, want in zip(current_rows, desired_rows, strict=True):
		for f in fields:
			if want.get(f) is None:
				if cur.get(f) not in (None, "", 0):
					return True
			elif not _same(cur.get(f), want.get(f)):
				return True
	return False


#: The varchar fieldtypes and their default width, for a ``frappe.db`` without ``type_map`` (the
#: bench-free stub). A real site reads Frappe's own map, so a framework change reaches this too.
VARCHAR_FIELDTYPES = {
	fieldtype: ("varchar", 140)
	for fieldtype in (
		"Data",
		"Link",
		"Dynamic Link",
		"Select",
		"Read Only",
		"Color",
		"Icon",
		"Phone",
		"Autocomplete",
	)
}


def _too_long(doctype, values, reference):
	"""The values Frappe would refuse as longer than their column, as its own messages.

	Frappe's ``Document._validate_length`` rule: a varchar field holds ``length`` characters, or
	the type's default (140) when ``length`` is 0; Small Text, Text and the rest are unbounded. A
	field the meta does not have is skipped, as Frappe skips it.
	"""
	meta = frappe.get_meta(doctype)
	type_map = getattr(frappe.db, "type_map", None) or VARCHAR_FIELDTYPES
	problems = []
	for field, value in values.items():
		if value in (None, ""):
			continue
		df = meta.get_field(field)
		if not df or df.fieldtype == "Check":
			continue
		column, size = type_map.get(df.fieldtype) or (None, None)
		if column != "varchar":
			continue
		limit = int(df.get("length") or 0) or int(size)
		if len(str(value)) > limit:
			problems.append(
				f"{reference}: '{df.label or field}' ({value}) will get truncated, "
				f"as max characters allowed is {limit}"
			)
	return problems


def _check_lengths(doctype, values, reference, child_doctype=None, rows=()):
	"""Raise CatalogRowError naming every value of a record (and its child rows) too long to store."""
	problems = _too_long(doctype, values, reference)
	for idx, row in enumerate(rows, start=1):
		problems += _too_long(child_doctype, row, f"{child_doctype}, Row {idx}")
	if problems:
		raise CatalogRowError(" ".join(problems))


def _user_or_none(user):
	return user if user and frappe.db.exists("User", user) else None


def _ensure_group(group):
	parent = dict(GROUPS.values()).get(group, "All Item Groups")
	if frappe.db.exists("Item Group", group):
		return
	if parent != "All Item Groups" and not frappe.db.exists("Item Group", parent):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": parent,
				"parent_item_group": "All Item Groups",
				"is_group": 1,
			}
		).insert(ignore_permissions=True)
	frappe.get_doc(
		{"doctype": "Item Group", "item_group_name": group, "parent_item_group": parent, "is_group": 0}
	).insert(ignore_permissions=True)


def _ensure_brand(name):
	if name and not frappe.db.exists("Brand", name):
		frappe.get_doc({"doctype": "Brand", "brand": name}).insert(ignore_permissions=True)


def find_item(item_plan):
	"""The existing Item for a plan, or None: the approval's code, else the catalog row id the
	loader stamped on it, else brand + model number.

	The catalog id comes before everything but an explicit ``approved.json`` code, so an Item
	renamed to its part number (:func:`rename_catalog_items`) is found under its new name rather
	than created again under the old rule's. An Item that merely holds the row's code is not
	this row's Item: :func:`apply_item` refuses the row rather than adopt it.
	"""
	code = item_plan["item_code"]
	if item_plan["match_existing_code"]:
		return code if frappe.db.exists("Item", code) else None
	if item_plan.get("catalog_id"):
		seeded = frappe.get_all(
			"Item",
			filters={"custom_catalog_id": item_plan["catalog_id"]},
			pluck="name",
			order_by="creation asc",
		)
		if seeded:
			return seeded[0]
	# Compared on catalog_key, the same normalization the approvals use, so a re-digitized
	# row whose model gained a space still finds its Item instead of creating a second one.
	want = catalog_key(item_plan["manufacturer"], item_plan["model"])
	for row in frappe.get_all(
		"Item",
		filters={"brand": item_plan["manufacturer"]},
		fields=["name", "brand", "custom_model_no"],
		order_by="creation asc",
	):
		if catalog_key(row.get("brand"), row.get("custom_model_no")) == want:
			return row["name"]
	return None


def site_codes():
	"""``{normalized code: Item name}`` for every Item on the site, compared as the naming guard does."""
	out = {}
	for name in frappe.get_all("Item", pluck="name"):
		out.setdefault(naming_rules.normalise(name), name)
	return out


def apply_item(kind, item_plan, verified_by, dry_run, taken=None):
	"""Create or update the Item. Returns ``(item_code, changes, created)``.

	``taken`` is :func:`site_codes`; a new Item whose code an existing Item already holds (once
	case and punctuation are ignored) is refused, never suffixed. The created code is added to it.
	"""
	values = dict(item_plan["set"])
	values["custom_spec_verified_by"] = verified_by
	existing = find_item(item_plan)
	if existing and item_plan["match_existing_code"] and item_plan.get("catalog_id"):
		# The loader's own Item for this row, still under its old code, has to be merged into the
		# approved one first. Loading now would stamp the row's id on a second Item and give the
		# approved Item a second set of curves.
		stale = [
			name
			for name in frappe.get_all(
				"Item", filters={"custom_catalog_id": item_plan["catalog_id"]}, pluck="name"
			)
			if name.casefold() != existing.casefold()
		]
		if stale:
			raise CatalogRowError(
				f"Item {stale[0]} still carries this row's catalog id. approved.json merges the row into "
				f"Item {existing}, so run rename_catalog_items first."
			)
	if not existing and taken is not None:
		holder = taken.get(naming_rules.normalise(item_plan["item_code"]))
		if holder:
			raise CatalogRowError(
				f"Item code {item_plan['item_code']!r} is already held by Item {holder}, which is not this "
				"row's Item. If it is the same part, set item_code in approved.json to attach the row to it."
			)
		taken[naming_rules.normalise(item_plan["item_code"])] = item_plan["item_code"]
	if not existing:
		new = {
			"item_code": item_plan["item_code"],
			**{k: v for k, v in item_plan["fill"].items() if v not in (None, "")},
			**{k: v for k, v in values.items() if v is not None},
		}
		_check_lengths("Item", new, f"Item {item_plan['item_code']}")
		if not dry_run:
			_ensure_group(item_plan["fill"]["item_group"])
			_ensure_brand(item_plan["fill"].get("brand"))
			doc = frappe.get_doc({"doctype": "Item", **new})
			doc.flags.ignore_naming_guard = True
			doc.insert(ignore_permissions=True)
			return doc.name, [f"Item {doc.name} created"], True
		return item_plan["item_code"], [f"Item {item_plan['item_code']} would be created"], True

	doc = frappe.get_doc("Item", existing)
	if not doc.get("brand") and not dry_run:
		_ensure_brand(item_plan["fill"].get("brand"))
	changed = _apply_fields(doc, values) + _apply_fields(doc, item_plan["fill"], fill_only=True)
	_check_lengths("Item", {f: doc.get(f) for f in changed}, f"Item {existing}")
	if changed and not dry_run:
		doc.save(ignore_permissions=True)
	return existing, ([f"Item {existing}: {', '.join(changed)}"] if changed else []), False


CURVE_POINT_FIELDS = ("flow_gpm", "head_ft", "efficiency_pct", "npshr_ft", "power_bhp")
NOZZLE_POINT_FIELDS = (
	"head_ft",
	"psi",
	"flow_gpm",
	"display_height_ft",
	"display_diameter_ft",
	"throw_ft",
	"depth_over_nozzle_in",
)


def apply_curve(item_code, curve, verified_by, dry_run, item_exists):
	"""Create or update one Pump Curve, keyed on Item + label. Returns the change lines."""
	fields = {k: v for k, v in curve.items() if k not in ("points", "notes")}
	fields["verified_by"] = verified_by
	name = None
	if item_exists:
		names = frappe.get_all(
			"Pump Curve", filters={"item": item_code, "curve_label": curve["curve_label"]}, pluck="name"
		)
		name = names[0] if names else None
	if not name:
		new = {
			"item": item_code,
			**{k: v for k, v in fields.items() if v is not None},
			"notes": curve.get("notes"),
		}
		points = [{f: p.get(f) for f in CURVE_POINT_FIELDS} for p in curve["points"]]
		_check_lengths("Pump Curve", new, f"Pump Curve ({curve['curve_label']})", "Pump Curve Point", points)
		if not dry_run:
			doc = frappe.get_doc({"doctype": "Pump Curve", **new, "points": points})
			doc.insert(ignore_permissions=True)
			return [f"Pump Curve {doc.name} ({curve['curve_label']}) created"]
		return [f"Pump Curve ({curve['curve_label']}) would be created"]
	doc = frappe.get_doc("Pump Curve", name)
	# Notes are filled once and then belong to whoever edits the curve.
	changed = _apply_fields(doc, fields) + _apply_fields(doc, {"notes": curve.get("notes")}, fill_only=True)
	points = []
	if _rows_differ(doc.get("points") or [], curve["points"], CURVE_POINT_FIELDS):
		changed.append("points")
		points = [{f: p.get(f) for f in CURVE_POINT_FIELDS} for p in curve["points"]]
		doc.set("points", [])
		for p in points:
			doc.append("points", p)
	_check_lengths(
		"Pump Curve",
		{f: doc.get(f) for f in changed if f != "points"},
		f"Pump Curve {name}",
		"Pump Curve Point",
		points,
	)
	if changed and not dry_run:
		doc.save(ignore_permissions=True)
	return [f"Pump Curve {name}: {', '.join(changed)}"] if changed else []


def find_profile(profile):
	"""The existing Nozzle Profile: by catalog row id, else manufacturer + model, else name."""
	if profile.get("catalog_id"):
		names = frappe.get_all("Nozzle Profile", filters={"catalog_id": profile["catalog_id"]}, pluck="name")
		if names:
			return names[0]
	names = frappe.get_all(
		"Nozzle Profile",
		filters={"manufacturer": profile["manufacturer"], "model_no": profile["model_no"]},
		pluck="name",
	)
	if names:
		return names[0]
	return profile["profile_name"] if frappe.db.exists("Nozzle Profile", profile["profile_name"]) else None


def apply_profile(item_code, plan, verified_by, dry_run):
	"""Create or update the Nozzle Profile, keyed on manufacturer + model. Returns the change lines."""
	profile = plan["profile"]
	fields = {k: v for k, v in profile.items() if k != "performance"}
	fields["item"] = item_code
	fields["verified_by"] = verified_by
	name = find_profile(profile)
	if not name:
		new = {
			**{k: v for k, v in fields.items() if v is not None},
			**{k: v for k, v in plan["profile_fill"].items() if v is not None},
		}
		points = [{f: p.get(f) for f in NOZZLE_POINT_FIELDS} for p in profile["performance"]]
		_check_lengths(
			"Nozzle Profile",
			new,
			f"Nozzle Profile {profile['profile_name']}",
			"Nozzle Performance Point",
			points,
		)
		if not dry_run:
			doc = frappe.get_doc({"doctype": "Nozzle Profile", **new, "performance": points})
			doc.insert(ignore_permissions=True)
			return [f"Nozzle Profile {doc.name} created"]
		return [f"Nozzle Profile {profile['profile_name']} would be created"]
	doc = frappe.get_doc("Nozzle Profile", name)
	fields.pop("profile_name", None)  # the name is the record's key; never renamed here
	changed = _apply_fields(doc, fields) + _apply_fields(doc, plan["profile_fill"], fill_only=True)
	points = []
	if _rows_differ(doc.get("performance") or [], profile["performance"], NOZZLE_POINT_FIELDS):
		changed.append("performance")
		points = [{f: p.get(f) for f in NOZZLE_POINT_FIELDS} for p in profile["performance"]]
		doc.set("performance", [])
		for p in points:
			doc.append("performance", p)
	_check_lengths(
		"Nozzle Profile",
		{f: doc.get(f) for f in changed if f != "performance"},
		f"Nozzle Profile {name}",
		"Nozzle Performance Point",
		points,
	)
	if changed and not dry_run:
		doc.save(ignore_permissions=True)
	return [f"Nozzle Profile {name}: {', '.join(changed)}"] if changed else []


# ----------------------------------------------------------------------------- purchasing
# Nikolas, 2026-11-02: each catalog Item says where to buy it and what that seller asks. The data
# is ``purchasing.json`` beside the catalog (one entry per row id, from public seller pages), and
# reaches the site as a Supplier per seller, an Item Supplier row and an Item Price on the buying
# list the file names ("Reference List 2026").

#: The fields one seller's entry may carry. Anything else is a typo, refused rather than ignored.
OFFER_FIELDS = {
	"seller",
	"seller_part_no",
	"url",
	"price_usd",
	"price_seen_on",
	"price_kind",
	"note",
	"seller_country",
	"search_note",
}
PRICE_KINDS = ("list", "sale")
#: A Supplier the loader creates. An existing Supplier is never edited.
NEW_SUPPLIER = {"supplier_group": "Distributor", "supplier_type": "Company", "country": "United States"}


class PurchasingRefusal(CatalogRowError):
	"""One seller of one row that is not loaded; the Item and the row's other sellers still are."""


def read_purchasing(data):
	"""``(meta, {ref: entry})`` from ``purchasing.json``.

	``meta`` is ``{price_list, currency}``. ``rows`` maps a row ``id`` (or a ``"Manufacturer|Model"``
	key, for a row without one) to its entry: one seller object, a list of them, or an object with
	only a ``search_note`` (what was searched when nothing was found).
	"""
	data = data or {}
	meta = {
		"price_list": str(data.get("price_list") or "").strip(),
		"currency": data.get("currency") or "USD",
	}
	rows = {}
	for ref, entry in (data.get("rows") or {}).items():
		if "|" in ref:
			manufacturer, _sep, model = ref.partition("|")
			ref = catalog_key(manufacturer, model)
		rows[ref.strip()] = entry
	return meta, rows


def check_offers(entry):
	"""The sellers in one row's purchasing entry, checked: ``[{seller, seller_part_no, url, price,
	seen_on, kind, note, country}]``. An entry with only a ``search_note`` has none.

	Raises :class:`PurchasingRefusal` naming what is wrong. Pure, so the bench-free suite checks the
	real file with it.
	"""
	offers = entry if isinstance(entry, list) else [entry]
	out = []
	for offer in offers:
		if not isinstance(offer, dict):
			raise PurchasingRefusal(f"a purchasing entry is {type(offer).__name__}, not an object")
		unknown = sorted(set(offer) - OFFER_FIELDS)
		if unknown:
			raise PurchasingRefusal(f"unknown purchasing field(s): {', '.join(unknown)}")
		seller = offer.get("seller")
		if seller in (None, ""):
			if any(offer.get(f) not in (None, "") for f in OFFER_FIELDS - {"search_note", "note"}):
				raise PurchasingRefusal("a purchasing entry without a seller")
			continue
		if not isinstance(seller, str) or seller != " ".join(seller.split()):
			raise PurchasingRefusal(f"seller {seller!r} has stray spaces")
		url = str(offer.get("url") or "").strip()
		if url and not url.startswith(("https://", "http://")):
			raise PurchasingRefusal(f"{seller}: url {url!r} is not a web address")
		price = offer.get("price_usd")
		seen_on = str(offer.get("price_seen_on") or "").strip()
		kind = str(offer.get("price_kind") or "").strip()
		if price in (None, ""):
			if seen_on or kind:
				raise PurchasingRefusal(f"{seller}: price_seen_on or price_kind without price_usd")
			price = None
		else:
			if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
				raise PurchasingRefusal(f"{seller}: price_usd {price!r} is not a positive number")
			try:
				datetime.date.fromisoformat(seen_on)
			except ValueError:
				raise PurchasingRefusal(
					f"{seller}: price_seen_on {seen_on!r} is not a YYYY-MM-DD date"
				) from None
			if kind not in PRICE_KINDS:
				raise PurchasingRefusal(
					f"{seller}: price_kind {kind!r} is not one of {', '.join(PRICE_KINDS)}"
				)
			price = float(price)
		if any(seller.casefold() == o["seller"].casefold() for o in out):
			raise PurchasingRefusal(f"{seller} is listed twice")
		out.append(
			{
				"seller": seller,
				"seller_part_no": _text(offer.get("seller_part_no")),
				"url": url or None,
				"price": price,
				"seen_on": seen_on or None,
				"kind": kind or None,
				"note": _text(offer.get("note")),
				"country": _text(offer.get("seller_country")) or NEW_SUPPLIER["country"],
			}
		)
	return out


def _supplier_key(name):
	"""A Supplier name with case and spaces ignored: two names equal here and not as written are
	the "differs only by case or spacing" refusal ("INYO Pools", "Inyo  pools", "InyoPools")."""
	return "".join(str(name or "").split()).casefold()


def supplier_book():
	"""``{"names": {name}, "keys": {key: name}}`` for every Supplier on the site."""
	names = set(frappe.get_all("Supplier", pluck="name"))
	keys = {}
	for name in sorted(names):
		keys.setdefault(_supplier_key(name), name)
	return {"names": names, "keys": keys}


def check_price_list(name, currency):
	"""None when Item Prices can go on the Price List ``name``, else the refusal. Never creates it."""
	if not name:
		return "purchasing.json names no price_list"
	found = frappe.get_all(
		"Price List", filters={"name": name}, fields=["name", "buying", "currency", "enabled"]
	)
	if not found or found[0].get("name") != name:
		return f"Price List {name!r} does not exist here. Create it, or name another in purchasing.json; no prices were loaded."
	row = found[0]
	if not row.get("enabled"):
		return f"Price List {name!r} is disabled; no prices were loaded."
	if not row.get("buying"):
		return f"Price List {name!r} is not a buying list; no prices were loaded."
	if row.get("currency") != currency:
		return f"Price List {name!r} is in {row.get('currency')}, not {currency}; no prices were loaded."
	return None


def ensure_supplier(offer, book, dry_run):
	"""The Supplier for a seller, found by its exact name or created. Returns ``(name, created)``.

	A name that matches an existing Supplier only once case and spacing are ignored ("Inyo pools"
	against "INYO Pools") is refused, never adopted or created alongside: which one is right is
	Nikolas's call.
	"""
	seller = offer["seller"]
	if seller in book["names"]:
		return seller, False
	near = book["keys"].get(_supplier_key(seller))
	if near:
		raise PurchasingRefusal(
			f"Supplier {seller!r} differs only by case or spacing from the existing Supplier {near!r}. "
			"Use the existing name in purchasing.json, or rename one of them, and run again."
		)
	values = {"supplier_name": seller, **NEW_SUPPLIER, "country": offer["country"]}
	_check_lengths("Supplier", values, f"Supplier {seller}")
	name = seller
	if not dry_run:
		doc = frappe.get_doc({"doctype": "Supplier", **values})
		doc.insert(ignore_permissions=True)
		name = doc.name
	book["names"].add(name)
	book["keys"][_supplier_key(name)] = name
	return name, True


def _price_note(offer, supplier):
	"""The Item Price's note: whose price it is, when it was seen, and where."""
	kind = "List price" if offer["kind"] == "list" else "List price (the page showed a sale)"
	parts = [f"{kind} advertised by {supplier}, seen {offer['seen_on']}."]
	if offer["note"]:
		parts.append(offer["note"])
	if offer["url"]:
		parts.append(offer["url"])
	return " ".join(parts)


def new_tally():
	return {
		"suppliers_created": [],
		"suppliers_used": [],
		"supplier_rows_added": 0,
		"supplier_rows_updated": 0,
		"prices_added": 0,
		"prices_updated": 0,
		"prices_kept": 0,
		"refused": [],
	}


def apply_purchasing(item_code, offers, ctx, dry_run, item_exists, stock_uom, tally):
	"""Suppliers, Item Supplier rows and Item Prices for one Item. Returns the change lines.

	``ctx`` is ``{book, price_list, currency, price_list_error, has_url}``. ``tally`` (:func:`new_tally`)
	counts what was (or would be) done and collects the refusals and the Suppliers created, for the
	run's summary. A refused seller is skipped and the rest of the row loads; anything else raises,
	and the caller rolls the whole row back, Suppliers created for it included.
	"""
	changes = []
	doc = frappe.get_doc("Item", item_code) if item_exists else None
	uom = (doc.get("stock_uom") if doc else None) or stock_uom
	doc_changed = False
	for offer in offers:
		try:
			supplier, created = ensure_supplier(offer, ctx["book"], dry_run)
		except PurchasingRefusal as exc:
			tally["refused"].append(str(exc))
			continue
		if created:
			tally["suppliers_created"].append(supplier)
			changes.append(f"Supplier {supplier} {'would be ' if dry_run else ''}created")
		tally["suppliers_used"].append(supplier)

		# The Item Supplier row: found by supplier, so the rows for other suppliers (Conely's
		# PEN022035, a person's own) are never touched.
		values = {"supplier_part_no": offer["seller_part_no"]}
		if ctx["has_url"]:
			values["purchase_url"] = offer["url"]
		_check_lengths("Item Supplier", {"supplier": supplier, **values}, f"Item Supplier {supplier}")
		row = (
			next((r for r in (doc.get("supplier_items") or []) if r.get("supplier") == supplier), None)
			if doc
			else None
		)
		if row is None:
			if doc is not None:
				doc.append("supplier_items", {"supplier": supplier, **{k: v for k, v in values.items() if v}})
				doc_changed = True
			tally["supplier_rows_added"] += 1
			changes.append(f"Item Supplier {supplier} {'would be ' if dry_run else ''}added")
		else:
			changed = []
			for field, value in values.items():
				if value is not None and not _same(row.get(field), value):
					setattr(row, field, value)
					changed.append(field)
			if changed:
				doc_changed = True
				tally["supplier_rows_updated"] += 1
				changes.append(f"Item Supplier {supplier}: {', '.join(changed)}")

		if offer["price"] is None:
			continue
		if ctx["price_list_error"]:
			continue
		changes += _apply_price(item_code, supplier, offer, ctx, uom, dry_run, item_exists, tally)

	if doc is not None and doc_changed and not dry_run:
		doc.save(ignore_permissions=True)
	return changes


def _apply_price(item_code, supplier, offer, ctx, uom, dry_run, item_exists, tally):
	"""Add or update the seller's Item Price; a stored price is replaced only by a later sighting."""
	price_list = ctx["price_list"]
	found = []
	if item_exists:
		found = frappe.get_all(
			"Item Price",
			filters={"item_code": item_code, "price_list": price_list, "supplier": supplier},
			fields=["name", "price_list_rate", "valid_from"],
			order_by="valid_from desc",
		)
	note = _price_note(offer, supplier)
	if not found:
		new = {
			"item_code": item_code,
			"price_list": price_list,
			"supplier": supplier,
			"uom": uom,
			"currency": ctx["currency"],
			"price_list_rate": offer["price"],
			"valid_from": offer["seen_on"],
			"note": note,
		}
		_check_lengths("Item Price", new, f"Item Price {supplier}")
		if not dry_run:
			frappe.get_doc({"doctype": "Item Price", **new}).insert(ignore_permissions=True)
		tally["prices_added"] += 1
		return [
			f"Item Price {price_list} {supplier} {offer['price']:g} {'would be ' if dry_run else ''}added"
		]
	current = found[0]
	stored_on = current.get("valid_from")
	stored_on = stored_on.isoformat() if hasattr(stored_on, "isoformat") else str(stored_on or "")
	# ISO dates compare as text. Same day or earlier: the stored price stands, even when it differs,
	# so an old file never overwrites a newer price someone entered.
	if stored_on and stored_on >= offer["seen_on"]:
		tally["prices_kept"] += 1
		return []
	if not dry_run:
		doc = frappe.get_doc("Item Price", current["name"])
		doc.price_list_rate = offer["price"]
		doc.valid_from = offer["seen_on"]
		doc.note = note
		doc.save(ignore_permissions=True)
	tally["prices_updated"] += 1
	return [
		f"Item Price {current['name']}: {_fmt(current.get('price_list_rate'))} -> {offer['price']:g} "
		f"(seen {offer['seen_on']})"
	]


def _empty_purchasing(price_list=None):
	return {
		"price_list": price_list,
		"suppliers_created": [],
		"suppliers_used": [],
		"supplier_rows_added": 0,
		"supplier_rows_updated": 0,
		"prices_added": 0,
		"prices_updated": 0,
		"prices_kept": 0,
		"rows_with_seller": 0,
		"rows_without_seller": 0,
		"refused": [],
		"not_in_catalog": [],
	}


def _add_tally(total, label, tally):
	for key in (
		"supplier_rows_added",
		"supplier_rows_updated",
		"prices_added",
		"prices_updated",
		"prices_kept",
	):
		total[key] += tally[key]
	total["suppliers_created"] += tally["suppliers_created"]
	total["suppliers_used"] = sorted(set(total["suppliers_used"]) | set(tally["suppliers_used"]))
	total["refused"] += [{"key": label, "error": error} for error in tally["refused"]]


def _empty_summary():
	return {
		"created": [],
		"updated": [],
		"unchanged": [],
		"unapproved": [],
		"approved_not_found": [],
		"errors": [],
		"warnings": [],
		"codes": {},
		"renames_pending": [],
	}


def _as_dry_run(dry_run):
	"""Only an explicit no writes: "0", "false", "no", "off", 0, False. Anything else, None
	included, stays a dry run."""
	if dry_run is None:
		return True
	if isinstance(dry_run, str):
		dry_run = dry_run.strip().casefold() not in ("0", "false", "no", "off")
	return bool(dry_run)


def read_catalog(folder):
	"""``(meta, approvals, entries)``: every row of the three files, each with its approval.

	An entry is ``{kind, key, base, label, row, approval, error}``: ``approval`` is the row's
	``approved.json`` overrides (``{}`` for a plain approval) or None when the row is not approved;
	``error`` is set when the row can only be approved by its id. ``code`` / ``code_error`` come
	from :func:`assign_codes`, which sees every row of every file at once.
	"""
	meta, approvals = read_approvals(_read_json(folder / APPROVAL_FILE))
	entries = []
	for kind, filename in FILES.items():
		approved = approvals.get(kind) or {"keys": {}, "ids": {}}
		by_key, by_id = approved["keys"], approved["ids"]
		for key, base, row in resolve_rows(rows_of(_read_json(folder / filename), kind)):
			row_id = str(row.get("id") or "").strip()
			entry = {"kind": kind, "key": key, "base": base, "label": row_id or key, "row": row}
			entry["approval"], entry["error"] = None, None
			if row_id and row_id in by_id:
				entry["approval"] = by_id[row_id]
			elif key in by_key:
				entry["approval"] = by_key[key]
			elif base != key and base in by_key:
				entry["error"] = f"{base} names {row_id or 'several rows'} and others; approve this row by its id"
			entries.append(entry)
	codes = assign_codes([(e["kind"], e["label"], e["row"], e["approval"]) for e in entries])
	for entry in entries:
		coded = codes[(entry["kind"], entry["label"])]
		entry["code"], entry["code_error"] = coded.get("code"), coded.get("error")
		entry["code_basis"] = coded.get("basis")
	return meta, approvals, entries


def _purchasing_context(folder):
	"""``(summary, ctx)`` for the run's purchasing step; both None without the file."""
	data = _read_json(folder / PURCHASING_FILE)
	if data is None:
		return None, None
	meta, rows = read_purchasing(data)
	buying = _empty_purchasing(meta["price_list"])
	ctx = {
		"rows": rows,
		"book": supplier_book(),
		"price_list": meta["price_list"],
		"currency": meta["currency"],
		"price_list_error": check_price_list(meta["price_list"], meta["currency"]),
		# A site field (Item Supplier, "Purchase URL"), not in this app's fixtures: without it a
		# link is reported, never written into nothing.
		"has_url": bool(frappe.get_meta("Item Supplier").get_field("purchase_url")),
	}
	if ctx["price_list_error"]:
		buying["refused"].append({"key": PURCHASING_FILE, "error": ctx["price_list_error"]})
	if not ctx["has_url"]:
		buying["refused"].append(
			{
				"key": PURCHASING_FILE,
				"error": "Item Supplier has no purchase_url field here; links not loaded",
			}
		)
	return buying, ctx


def load_catalog(catalog_dir=None, dry_run=True):
	"""Load the approved catalog rows. ``dry_run`` (the default) writes nothing.

	Returns ``{dry_run, catalog_dir, approved_by, approved_on, pumps, nozzles, lights}``; each
	kind is ``{created, updated, unchanged, unapproved, approved_not_found, errors, warnings,
	codes, renames_pending}``. ``created``/``updated`` hold ``{key, item_code, changes}``, the
	others keys (or ``{key, error}``). ``codes`` is ``{key: item code}`` for every approved row,
	the code it is created under; ``renames_pending`` lists the Items that already exist under
	another code (``{key, item_code, code}``), which :func:`rename_catalog_items` renames. A
	missing data file is an empty kind, not an error.
	"""
	dry_run = _as_dry_run(dry_run)
	folder = Path(catalog_dir) if catalog_dir else CATALOG_DIR
	meta, approvals, entries = read_catalog(folder)
	verified_by = _user_or_none(meta["approved_by"])
	summary = {
		"dry_run": dry_run,
		"catalog_dir": str(folder),
		"approved_by": meta["approved_by"],
		"approved_on": meta["approved_on"],
	}
	if meta["approved_by"] and not verified_by:
		summary["note"] = f"approved_by {meta['approved_by']} is not a User here; verified_by left blank"
	taken = site_codes()
	buying, ctx = _purchasing_context(folder)
	summary["purchasing"] = buying

	for kind in FILES:
		out = summary[kind] = _empty_summary()
		approved = approvals.get(kind) or {"keys": {}, "ids": {}}
		by_key, by_id = approved["keys"], approved["ids"]
		seen_keys, seen_ids = set(), set()
		for entry in (e for e in entries if e["kind"] == kind):
			key, base, row, label = entry["key"], entry["base"], entry["row"], entry["label"]
			seen_keys.update((key, base))
			seen_ids.add(str(row.get("id") or "").strip())
			if entry["error"]:
				out["errors"].append({"key": label, "error": entry["error"]})
				continue
			if entry["approval"] is None:
				out["unapproved"].append(label)
				continue
			if entry["code_error"]:
				out["errors"].append({"key": label, "error": entry["code_error"]})
				continue
			out["codes"][label] = entry["code"]
			try:
				plan = PLANNERS[kind](row, entry["approval"], meta, entry["code"])
			except CatalogRowError as exc:
				out["errors"].append({"key": label, "error": str(exc)})
				continue
			out["warnings"] += [f"{label}: {w}" for w in plan["warnings"]]
			offers, tally = [], new_tally()
			if ctx is not None:
				ref = label if label in ctx["rows"] else key
				try:
					offers = check_offers(ctx["rows"][ref]) if ref in ctx["rows"] else []
				except PurchasingRefusal as exc:
					buying["refused"].append({"key": label, "error": str(exc)})
			try:
				if not dry_run:
					frappe.db.savepoint("catalog_seed_row")
				item_code, changes, created = apply_item(kind, plan["item"], verified_by, dry_run, taken)
				if kind == "pumps":
					for curve in plan["curves"]:
						changes += apply_curve(
							item_code, curve, verified_by, dry_run, item_exists=not created
						)
					# The designer's pump list (api.designer._pump_items) reads item_group "Pumps" or
					# equipment class "Pump", and the loader sets the class, so an Item outside the
					# group is still offered. Said anyway: the group is Sapphire's to choose, and a
					# person filtering the Item list by "Pumps" will not see it.
					group = (
						plan["item"]["fill"]["item_group"]
						if created
						else frappe.db.get_value("Item", item_code, "item_group")
					)
					if group != GROUPS["pumps"][0]:
						out["warnings"].append(
							f"{label}: Item {item_code} is in item group {group!r}, not {GROUPS['pumps'][0]!r}; "
							"the designer still offers it by its equipment class (Pump)"
						)
				elif kind == "nozzles" and plan.get("profile"):
					changes += apply_profile(item_code, plan, verified_by, dry_run)
				if offers:
					# A dry run that would create the Item has no Item to read; a write has just made it.
					changes += apply_purchasing(
						item_code,
						offers,
						ctx,
						dry_run,
						item_exists=not (created and dry_run),
						stock_uom=plan["item"]["fill"]["stock_uom"],
						tally=tally,
					)
			except Exception as exc:
				if not dry_run:
					frappe.db.rollback(save_point="catalog_seed_row")
				# The row's Suppliers went with the rollback, so the next row must not count them as there.
				for name in tally["suppliers_created"]:
					ctx["book"]["names"].discard(name)
					ctx["book"]["keys"].pop(_supplier_key(name), None)
				out["errors"].append({"key": label, "error": str(exc) or exc.__class__.__name__})
				continue
			if ctx is not None:
				_add_tally(buying, label, tally)
				buying["rows_with_seller" if offers else "rows_without_seller"] += 1
			entry_out = {"key": label, "item_code": item_code, "changes": changes}
			if created:
				out["created"].append(entry_out)
			elif changes:
				out["updated"].append(entry_out)
			else:
				out["unchanged"].append(label)
			# Case alone is no rename: MariaDB compares names case-insensitively, as the guard does.
			if item_code.casefold() != plan["item"]["item_code"].casefold() and not plan["item"]["match_existing_code"]:
				out["renames_pending"].append({"key": label, "item_code": item_code, "code": plan["item"]["item_code"]})
		out["approved_not_found"] = sorted(set(by_key) - seen_keys) + sorted(set(by_id) - seen_ids)
	if ctx is not None:
		all_refs = {e["label"] for e in entries} | {e["key"] for e in entries}
		buying["not_in_catalog"] = sorted(set(ctx["rows"]) - all_refs)

	if not dry_run:
		frappe.db.commit()
	return summary


@frappe.whitelist(methods=["POST"])
def seed_catalog(dry_run=1):
	"""System Manager: :func:`load_catalog` from the repo's catalog folder. ``dry_run`` defaults to 1.

	``dry_run`` goes through raw: ``load_catalog`` writes only for an explicit no ("0", "false",
	"no", "off", 0, False). ``cint`` here would turn ``frappe.call``'s ``"true"`` (and "yes", "on")
	into 0, a dry run requested by name becoming a write of every approved row.
	"""
	frappe.only_for("System Manager")
	return load_catalog(dry_run=dry_run)


# ----------------------------------------------------------------------------- renaming
# Nikolas, 2026-11-02: the catalog Items' codes are the manufacturer's part numbers. The 380
# Items loaded that day carry the old rule's codes (PUMP-PENTAIR-EQ500-EQK500), so this renames
# them, in rounds, from a person's request: nothing here runs on migrate and nothing is enqueued.

#: Items renamed per call unless the caller says otherwise. A rename rewrites every Link to Item
#: (140 fields on erpnext 16.52.1) and clears the cache, so a round is kept well inside a request.
RENAME_LIMIT = 25
#: Renames committed together, with the design rewrite that goes with them.
RENAME_BATCH = 5
#: The Water Feature Design fields that hold the design's JSON, any of which may name an Item code.
DESIGN_JSON_FIELDS = ("model_json", "results_json", "design_issues_json", "readiness_json")


def catalog_codes(folder=None):
	"""``{catalog id: {kind, code, legacy_code, basis, merge, values} | {kind, error}}`` for every
	row with an id.

	``kind`` is the Item's kind (``pumps``, ``nozzles``, ``aerators``, ``lights``). ``legacy_code``
	is the code the old rule gave the row, or None when the row cannot be planned. ``merge`` is
	true when ``approved.json`` names the row's Item (``item_code``), which makes an existing Item
	of that code the row's Item. ``values`` are the Item values the loader writes for the row: its
	specs and provenance, exactly as :func:`apply_item` would write them, with every None left out
	because the loader never writes one. A merge writes these onto that Item.
	"""
	meta, _approvals, entries = read_catalog(Path(folder) if folder else CATALOG_DIR)
	verified_by = _user_or_none(meta["approved_by"])
	out = {}
	for entry in entries:
		row_id = str(entry["row"].get("id") or "").strip()
		if not row_id:
			continue
		kind, legacy, values = entry["kind"], None, {}
		try:
			plan = PLANNERS[entry["kind"]](entry["row"], entry["approval"] or {}, meta, entry["code"])
			kind = "aerators" if plan["item"]["fill"]["item_group"] == GROUPS["aerators"][0] else kind
			legacy = plan["item"]["legacy_code"]
			values = dict(plan["item"]["set"])
			values["custom_spec_verified_by"] = verified_by
			values = {field: value for field, value in values.items() if value is not None}
		except CatalogRowError:
			pass
		if entry["code_error"]:
			out[row_id] = {"kind": kind, "error": entry["code_error"], "legacy_code": legacy}
		else:
			out[row_id] = {
				"kind": kind,
				"code": entry["code"],
				"basis": entry["code_basis"],
				"legacy_code": legacy,
				"merge": bool((entry["approval"] or {}).get("item_code")),
				"values": values,
			}
	return out


def plan_renames(items, codes, existing_names):
	"""``{renames, merges, unchanged, refused}`` for the Items that carry a catalog id. Pure.

	``items`` is ``[{name, custom_catalog_id}]``, ``codes`` is :func:`catalog_codes`, and
	``existing_names`` every Item name on the site. A rename is ``{old, new, kind, catalog_id,
	basis}``. Refused, with the reason: an id no catalog row has; an id two Items carry; a row whose
	code collides with another row's; an Item whose current code is not the one the old rule gave
	its row (a Sapphire Item attached to the row, which is renamed by a person or not at all); a
	new code another Item already holds once case and punctuation are ignored.

	A merge has the same shape. It is planned where ``approved.json`` gives the row an
	``item_code`` that an existing Item holds exactly (case aside), and the loader's Item for the
	row still sits under its old code: that Item is folded into the existing one
	(:func:`merge_item`) rather than renamed onto it. Nikolas, 2026-11-02, for the three Pentair
	pumps Sapphire already stocked under their part numbers. The existing Item is refused when it
	is another catalog row's Item.
	"""
	by_id, id_of = {}, {}
	for item in items:
		catalog_id = str(item.get("custom_catalog_id") or "").strip()
		by_id.setdefault(catalog_id, []).append(item["name"])
		id_of[item["name"].casefold()] = catalog_id
	holders = {}
	for name in existing_names:
		holders.setdefault(naming_rules.normalise(name), []).append(name)
	out = {"renames": [], "merges": [], "unchanged": [], "refused": []}
	for catalog_id in sorted(by_id):
		names = sorted(by_id[catalog_id])
		coded = codes.get(catalog_id)
		target = None
		if coded and coded.get("merge") and not coded.get("error"):
			target = next(
				(n for n in holders.get(naming_rules.normalise(coded["code"]), [])
				 if n.casefold() == coded["code"].casefold()),
				None,
			)
		rivals = [n for n in names if not target or n.casefold() != target.casefold()]
		for old in names:
			kind = (coded or {}).get("kind")
			base = {"old": old, "kind": kind, "catalog_id": catalog_id}
			if not coded:
				out["refused"].append({**base, "reason": "no row in the catalog files has this catalog id"})
			elif target and old.casefold() == target.casefold():
				out["unchanged"].append(base)
			elif len(rivals) > 1:
				out["refused"].append({**base, "reason": f"{len(names)} Items carry this catalog id: {', '.join(names)}"})
			elif coded.get("error"):
				out["refused"].append({**base, "reason": coded["error"]})
			elif old == coded["code"]:
				out["unchanged"].append(base)
			elif old != coded.get("legacy_code"):
				out["refused"].append(
					{**base, "new": coded["code"],
					 "reason": f"{old} is not the code the loader gave this row ({coded.get('legacy_code')}); "
					 "rename it by hand, or set item_code in approved.json to keep it"}
				)
			elif target and id_of.get(target.casefold(), catalog_id) != catalog_id:
				out["refused"].append(
					{**base, "new": target,
					 "reason": f"approved.json merges this row into Item {target}, which is already the Item "
					 f"of catalog row {id_of[target.casefold()]}"}
				)
			elif target:
				out["merges"].append({**base, "new": target, "basis": coded["basis"]})
			else:
				others = [n for n in holders.get(naming_rules.normalise(coded["code"]), []) if n != old]
				if others:
					out["refused"].append(
						{**base, "new": coded["code"],
						 "reason": f"Item {others[0]} already holds the code {coded['code']!r} "
						 "(the same once case and punctuation are ignored)"}
					)
				else:
					out["renames"].append({**base, "new": coded["code"], "basis": coded["basis"]})
	return out


def _rewrite_text(text, mapping):
	"""``text`` with each ``|``-separated part equal to an old code replaced: a whole value
	(``"PUMP-A"``) or one part of a composite key (``"nozzle|PUMP-A"``). Returns ``(text, count)``."""
	parts = text.split("|")
	count = sum(1 for p in parts if p in mapping)
	return ("|".join(mapping.get(p, p) for p in parts), count) if count else (text, 0)


def rewrite_codes(value, mapping):
	"""``(value, count)``: a JSON value with every old Item code in ``mapping`` replaced by its new one.

	Walks the whole tree, keys included, because a design names Items in more places than one
	(``network.pumps[].item``, ``network.pumps[].curve`` when the curve is named by its Item,
	``equipment[].item``, ``features[].product.item``, BOM line keys and item hints in the derived
	JSON). A string is replaced only where all of it, or one ``|``-part of it, is an old code, so
	text that merely contains one (a description) is left as written.
	"""
	if isinstance(value, dict):
		out, count = {}, 0
		for key, item in value.items():
			new_key, n_key = _rewrite_text(key, mapping) if isinstance(key, str) else (key, 0)
			new_item, n_item = rewrite_codes(item, mapping)
			out[new_key] = new_item
			count += n_key + n_item
		return out, count
	if isinstance(value, list):
		pairs = [rewrite_codes(v, mapping) for v in value]
		return [v for v, _n in pairs], sum(n for _v, n in pairs)
	if isinstance(value, str):
		return _rewrite_text(value, mapping)
	return value, 0


def rewrite_designs(mapping, dry_run):
	"""Rewrite every Water Feature Design whose JSON names an old code. Returns ``[{design, fields,
	replaced}]``.

	``frappe.rename_doc`` rewrites Link fields only; a design names its pump inside ``model_json``
	text, so without this a renamed pump drops out of every design that picked it. Written with
	``db.set_value`` (no derive, ``modified`` kept) and a Comment on the design saying what changed.
	"""
	if not mapping:
		return []
	out = []
	fields = ["name", *DESIGN_JSON_FIELDS]
	for design in frappe.get_all("Water Feature Design", fields=fields):
		changed, before, replaced = {}, {}, 0
		for field in DESIGN_JSON_FIELDS:
			value = design.get(field)
			# A JSON column can come back parsed rather than as text, depending on the field type and
			# the driver; both are read, and written back as text.
			if isinstance(value, (dict, list)):
				text = json.dumps(value)
			elif isinstance(value, str) and value:
				text = value
				try:
					value = json.loads(text)
				except ValueError:
					continue
			else:
				continue
			if not any(old in text for old in mapping):
				continue
			new, count = rewrite_codes(value, mapping)
			if count:
				changed[field], before[field], replaced = json.dumps(new), text, replaced + count
		if not changed:
			continue
		out.append({"design": design["name"], "fields": sorted(changed), "replaced": replaced})
		if dry_run:
			continue
		for field, text in changed.items():
			frappe.db.set_value("Water Feature Design", design["name"], field, text, update_modified=False)
		named = sorted({old for old in mapping if any(old in before[f] for f in changed)})
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Water Feature Design",
				"reference_name": design["name"],
				"content": "Catalog Item codes renamed to the manufacturer's part numbers: "
				+ ", ".join(f"{old} → {mapping[old]}" for old in named),
			}
		).insert(ignore_permissions=True)
	return out


#: The Links a merge moves to the Item it merges into. Any other Link to the old Item refuses the merge.
MERGE_MOVES = (("Pump Curve", "item"), ("Nozzle Profile", "item"))


def merge_links(old):
	"""``(moves, linked)`` for folding Item ``old`` into another.

	``moves`` is ``{doctype: [names]}``: the Pump Curves and Nozzle Profiles that the merge
	re-points. ``linked`` lists every other Link to ``old``, as ``"Doctype.field (n)"``, across
	every Link-to-Item field ``rename_doc`` itself would update. Anything in ``linked`` refuses
	the merge. The old Item is deleted, so it is never left behind as a second copy, and it is
	never deleted while a Link still points at it.

	This list is the reason a refusal is useful. ``frappe.delete_doc`` checks links too, but on a
	doctype with a ``disabled`` field it swaps the linked record for "You can disable this Item
	instead of deleting it". It also runs ``Item.on_trash`` *before* that check, and on_trash
	deletes the Item's Bins and Item Prices. Both are Link fields here, so they refuse the merge
	before ``delete_doc`` is ever reached.
	"""
	from frappe.model.rename_doc import get_link_fields

	moves = {doctype: frappe.get_all(doctype, filters={field: old}, pluck="name") for doctype, field in MERGE_MOVES}
	linked = []
	for link in get_link_fields("Item"):
		parent, field = link["parent"], link["fieldname"]
		if (parent, field) in MERGE_MOVES:
			continue
		try:
			if link.get("issingle"):
				count = 1 if frappe.db.get_single_value(parent, field) == old else 0
			else:
				count = frappe.db.count(parent, {field: old})
		except Exception:
			linked.append(f"{parent}.{field} (could not be checked)")
			continue
		if count:
			linked.append(f"{parent}.{field} ({count})")
	return {doctype: names for doctype, names in moves.items() if names}, linked


def merge_writes(target, values):
	"""``{field: {"from": current, "to": new}}``: what a merge would change on Item ``target``.

	``values`` are the catalog's own values for the row, from :func:`catalog_codes`. They are never
	read from the duplicate Item. A field the catalog leaves unset sits on the duplicate as Frappe's
	0, and copying that would overwrite Sapphire's figure. 342002's 1.5 HP is the case in point:
	its duplicate holds 0.0. Each later seed then writes the same values again, so this is also
	what the target will read from then on.
	"""
	doc = frappe.get_doc("Item", target)
	return {
		field: {"from": doc.get(field), "to": value}
		for field, value in values.items()
		if not _same(doc.get(field), value)
	}


def merge_item(merge, values, moves):
	"""Fold the loader's Item ``merge["old"]`` into the existing Item ``merge["new"]``.

	The steps are:
	1. Write the catalog's own ``values`` onto the existing Item, as the loader would (see
	   :func:`merge_writes`). Its name, group, brand and description stay Sapphire's.
	2. Re-point the ``moves`` from :func:`merge_links`. A Pump Curve's fetched ``item_name`` goes
	   with it, because ``db.set_value`` does not re-fetch it.
	3. Delete the old Item. ``delete_doc`` is never forced, so a Link that has appeared since the
	   check still refuses the merge.

	Returns the fields changed on the existing Item.
	"""
	item_name = frappe.db.get_value("Item", merge["new"], "item_name")
	for doctype, names in moves.items():
		update = {"item": merge["new"]}
		if doctype == "Pump Curve":
			update["item_name"] = item_name
		for name in names:
			frappe.db.set_value(doctype, name, update)
	frappe.delete_doc("Item", merge["old"], ignore_permissions=True)
	doc = frappe.get_doc("Item", merge["new"])
	changed = _apply_fields(doc, values)
	if changed:
		doc.save(ignore_permissions=True)
	return changed


def rename_items(dry_run=True, limit=RENAME_LIMIT, catalog_dir=None):
	"""Rename the catalog Items to the codes the catalog data gives them, and fold into its existing
	Item each one that ``approved.json`` merges. ``dry_run`` writes nothing.

	Returns ``{dry_run, renames, merges, renamed, merged, unchanged, refused, errors, remaining,
	designs, seconds}``.
	* ``renames`` is every rename still to do: ``{old, new, kind, catalog_id, basis}``.
	* ``merges`` is every merge still to do, in the same shape plus three keys:
	  - ``moves``: the Pump Curves and Nozzle Profiles that follow, from :func:`merge_links`;
	  - ``writes``: each field value the merge changes on the existing Item, from :func:`merge_writes`;
	  - ``delete: True``: nothing else links to the old Item, so it is deleted.
	* A merge whose old Item something else links to is refused instead, and carries ``linked``.

	A write does the first ``limit`` of these, merges first (``merged``, then ``renamed``), with one
	savepoint per Item. It commits every :data:`RENAME_BATCH`, together with the design rewrite
	those need, and ``remaining`` says how many are left for the next round. Each round re-plans
	from the site, so a round that stopped halfway is simply run again.

	``designs`` is the design rewrite (:func:`rewrite_designs`). Its old -> new map covers every
	catalog Item whose current name is not the old rule's code, not only this round's. A design
	saved with an old code between rounds is therefore still repaired by the next one. A merged Item
	is covered as well, because the Item it merged into carries its catalog id. That includes a write
	round with nothing left to rename or merge: it still repairs the designs, and is the way to fix
	one saved with an old code after the last round.
	"""
	from frappe.model.rename_doc import rename_doc

	from erpnext_enhancements.water_engineering.api.designer import CATALOG_CACHE_KEY

	dry_run = _as_dry_run(dry_run)
	limit = max(int(limit or 0), 0) or RENAME_LIMIT
	started = time.monotonic()
	codes = catalog_codes(catalog_dir)
	every = frappe.get_all("Item", fields=["name", "custom_catalog_id"])
	items = [i for i in every if str(i.get("custom_catalog_id") or "").strip()]
	plan = plan_renames(items, codes, [i["name"] for i in every])
	merges, refused = [], list(plan["refused"])
	for merge in plan["merges"]:
		moves, linked = merge_links(merge["old"])
		if linked:
			refused.append(
				{**merge, "linked": linked,
				 "reason": f"approved.json merges {merge['old']} into Item {merge['new']}, but {merge['old']} "
				 f"cannot be deleted: {', '.join(linked)} link to it"}
			)
		else:
			writes = merge_writes(merge["new"], codes[merge["catalog_id"]]["values"])
			merges.append({**merge, "moves": moves, "writes": writes, "delete": True})
	summary = {
		"dry_run": dry_run,
		"renames": plan["renames"],
		"merges": merges,
		"renamed": [],
		"merged": [],
		"unchanged": plan["unchanged"],
		"refused": refused,
		"errors": [],
		"designs": [],
	}
	# Every catalog Item already under a new code, by the old rule's code: the designs still
	# naming one are repaired whatever round renamed it.
	done = {
		codes[i["custom_catalog_id"]]["legacy_code"]: i["name"]
		for i in items
		if codes.get(i["custom_catalog_id"], {}).get("legacy_code")
		and codes[i["custom_catalog_id"]]["legacy_code"] != i["name"]
	}
	work = [("merge", m) for m in merges] + [("rename", r) for r in plan["renames"]]
	todo = work[:limit]
	if dry_run:
		summary["designs"] = rewrite_designs({**done, **{w["old"]: w["new"] for _op, w in work}}, True)
		summary["remaining"] = len(work)
		summary["seconds"] = round(time.monotonic() - started, 2)
		return summary
	for start in range(0, len(todo), RENAME_BATCH):
		mapping = {}
		for op, entry in todo[start : start + RENAME_BATCH]:
			frappe.db.savepoint("catalog_rename_item")
			try:
				if op == "merge":
					changed = merge_item(entry, codes[entry["catalog_id"]]["values"], entry["moves"])
				else:
					# The model function, not frappe.rename_doc: at v16.53.0 that wrapper takes no
					# ignore_permissions, and passing it is a TypeError on every Item.
					rename_doc(
						doctype="Item",
						old=entry["old"],
						new=entry["new"],
						ignore_permissions=True,
						show_alert=False,
						rebuild_search=False,
					)
					# rebuild_search would enqueue a rebuild of every Item; move this one's row instead.
					frappe.db.sql(
						"update `__global_search` set name = %s where doctype = 'Item' and name = %s",
						(entry["new"], entry["old"]),
					)
			except Exception as exc:
				frappe.db.rollback(save_point="catalog_rename_item")
				summary["errors"].append({**entry, "error": str(exc) or exc.__class__.__name__})
				continue
			mapping[entry["old"]] = entry["new"]
			if op == "merge":
				summary["merged"].append({**entry, "fields": changed})
			else:
				summary["renamed"].append(entry)
		summary["designs"] += rewrite_designs({**done, **mapping}, False)
		done.update(mapping)
		frappe.db.commit()
		if mapping:
			# The designer keeps its pump catalog, codes and all, for ten minutes, and rename_doc's
			# clear_cache does not reach that key: a pick in that window would save an old code.
			frappe.cache().delete_value(CATALOG_CACHE_KEY)
	if not todo:
		# Nothing left to rename, but a design saved with an old code since the last round still
		# needs repairing. The batches above repair only alongside a rename, so a round that has
		# none does the repair here.
		summary["designs"] = rewrite_designs(done, False)
		if summary["designs"]:
			frappe.db.commit()
	summary["remaining"] = len(work) - len(summary["renamed"]) - len(summary["merged"])
	summary["seconds"] = round(time.monotonic() - started, 2)
	return summary


@frappe.whitelist(methods=["POST"])
def rename_catalog_items(dry_run=1, limit=RENAME_LIMIT):
	"""System Manager: :func:`rename_items`. ``dry_run`` defaults to 1, and like
	:func:`seed_catalog` only an explicit no writes; ``limit`` is the Items renamed or merged this round."""
	frappe.only_for("System Manager")
	return rename_items(dry_run=dry_run, limit=limit)
