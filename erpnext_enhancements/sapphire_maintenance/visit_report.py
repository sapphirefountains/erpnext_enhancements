# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What the customer's visit report shows beyond the record's own fields (v1.597.0, D1).

The report is the "Maintenance Record Print" format (``fixtures/print_format.json``): the
PDF the customer is emailed when a visit is approved, and what Lisa reviews. Reading the
first one (2hkckpg374, Myers) on 2026-10-12 she asked for:

* **names, not IDs**: the Water Feature column printed ``MAINT-MYERS-Fountain``. Every water
  feature's Serial No has a proper ``item_name`` ("Hardware Apartments - East Courtyard
  Fountain"); the report prints the part after the site ("East Courtyard Fountain"), since the
  site is already in the header, and the header lists the fountains a site visit covers;
* **the contract year**: "Sanctuary water fall Maintenance contract 25-26". Nik chose the
  calendar year of the visit, so the line reads "Myers Mortuary Maintenance Contract ·
  2026" (contract start dates in ERPNext are the day each contract was entered);
* **a graph of the water**: each reading over the site's recent approved visits and this one,
  with the target range shaded, one chart per reading and one line per water feature;
* **a photo of their fountain**: the "fountain as we left it" photo the technician takes at
  wrap-up (``fountain_photo``).

The print sandbox cannot query or draw, so the template calls :func:`maintenance_report` once
(a Jinja global, hooks.py) and lays the answer out. **It never raises**: the PDF is made in the
finalize job and emailed, and a chart that fails must not cost the customer the report. Each
part fails on its own, logged, and the report prints without it.

**It reads the visit as saved, never the document it is handed.** A Jinja global is callable
from any template on the site, and v16's print view also renders a document a caller posts as
JSON, so a ``fountain_photo`` naming someone else's private file would otherwise be inlined into
the caller's PDF. :func:`_saved` loads the visit again by name and checks the caller may read it
(the trip sheet's ``ee_trip_sheet`` and the knowledge base's ``kb_document`` do the same), and a
photo is inlined only when it is a File attached to that visit, as the wizard and the form both
attach it. Anything else prints the report without these parts.

**Images are inlined** as data URIs, shrunk to fit a page: the finalize PDF is made in a job
with no session, and the headless browser fetches an ``<img>`` over the network, so a private
file's URL prints as a broken image there (v16 chrome PDF; see CLAUDE.md's PDF notes).
"""

import base64
import io
import mimetypes
import os

import frappe

from erpnext_enhancements import print_style as ps
from erpnext_enhancements.sapphire_maintenance import visit_compare

DOCTYPE = "Sapphire Maintenance Record"
#: Points on a chart: this visit and up to this many before it, per water feature.
CHART_POINTS = 6
#: One line per water feature, in the print design system's own colours.
SERIES_COLOURS = (ps.FRESH_BLUE_DEEP, ps.VIOLET_DEEP, ps.TEAL_DEEP, ps.DEEP_SEA_BLUE)
#: The longest side of an inlined photo, and the most bytes of one that cannot be shrunk.
PHOTO_SIDE = 1200
PHOTO_MAX_BYTES = 4 * 1024 * 1024


def maintenance_report(doc):
	"""Everything the visit report prints that the record does not hold itself.

	``{"features": {serial: name}, "feature_lines": [name, ...], "contract_line": str,
	"charts": [svg markup], "fountain_photo": data URI or None}``. Never raises.
	"""
	report = {"features": {}, "feature_lines": [], "contract_line": "", "charts": [], "fountain_photo": None}
	try:
		doc = _saved(doc)
	except Exception:
		_log("record")
		return report
	if doc is None:
		return report
	try:
		report["features"] = feature_names(doc)
		report["feature_lines"] = [
			report["features"].get(serial) or serial for serial in covered_features(doc)
		]
	except Exception:
		_log("names")
	try:
		report["contract_line"] = contract_line(doc)
	except Exception:
		_log("contract line")
	try:
		report["charts"] = chemistry_charts(doc, report["features"])
	except Exception:
		_log("charts")
	try:
		report["fountain_photo"] = image_data_uri(doc.get("fountain_photo"), doc.name)
	except Exception:
		_log("fountain photo")
	return report


def _saved(doc):
	"""The visit ``doc`` names, loaded again, or None: not a visit, not saved, or one the caller
	may not read. Only the name is taken from ``doc`` (see the module docstring)."""
	read = doc.get if hasattr(doc, "get") else lambda key: getattr(doc, key, None)
	name = read("name")
	if read("doctype") != DOCTYPE or not isinstance(name, str) or not name:
		return None
	if not frappe.db.exists(DOCTYPE, name):
		return None
	saved = frappe.get_doc(DOCTYPE, name)
	if not frappe.has_permission(DOCTYPE, "read", doc=saved):
		return None
	return saved


def _log(part):
	try:
		frappe.log_error(title=f"Visit report: {part} not printed", message=frappe.get_traceback())
	except Exception:
		pass


# ---------------------------------------------------------------------- names


def short_name(item_name):
	"""``"Hardware Apartments - East Courtyard Fountain"`` -> ``"East Courtyard Fountain"``.

	The part after the first " - " (the site comes first in every water feature's name); the
	whole name when there is no site part.
	"""
	item_name = (item_name or "").strip()
	if " - " in item_name:
		return item_name.split(" - ", 1)[1].strip() or item_name
	return item_name


def covered_features(doc):
	"""The water features this visit covers, in form order: the record's own, or each one its
	rows are tagged with on a site visit."""
	if doc.get("serial_no"):
		return [doc.serial_no]
	seen = []
	for table in ("chemistry_readings", "cleaning_tasks", "maintenance_results", "consumables"):
		for row in doc.get(table) or []:
			serial = row.get("serial_no")
			if serial and serial not in seen:
				seen.append(serial)
	return seen


def feature_names(doc):
	"""``{serial_no: short name}`` for every water feature the visit mentions."""
	serials = set(covered_features(doc))
	if not serials:
		return {}
	return {
		row.get("name"): short_name(row.get("item_name"))
		for row in frappe.get_all(
			"Serial No", filters={"name": ["in", sorted(serials)]}, fields=["name", "item_name"]
		)
		if row.get("item_name")
	}


def contract_line(doc):
	"""``"Myers Mortuary Maintenance Contract · 2026"``: the project and the visit's calendar year."""
	if not doc.get("project"):
		return ""
	name = frappe.db.get_value("Project", doc.project, "project_name") or doc.project
	return f"{name} · {visit_compare.visit_day(doc).year}"


# ---------------------------------------------------------------------- charts


def chemistry_charts(doc, names=None):
	"""One SVG per reading this visit took: its value at the site's recent approved visits and
	this one, a line per water feature, the target range shaded. Readings with fewer than two
	points anywhere are left out: one point is not a trend."""
	names = names or {}
	rows = [row for row in doc.get("chemistry_readings") or [] if visit_compare.measured(row)]
	if not rows:
		return []

	readings = {}  # label key -> {"title", "uom", "low", "high", "series": {feature: [(day, value, flagged)]}}
	order = []
	day = visit_compare.visit_day(doc)
	for row in rows:
		feature, key = visit_compare.reading_key(doc, row)
		if key not in readings:
			readings[key] = {
				"title": row.get("reading") or "",
				"uom": row.get("uom") or "",
				"low": row.get("min_value"),
				"high": row.get("max_value"),
				"series": {},
			}
			order.append(key)
		readings[key]["series"].setdefault(feature, []).append(
			(day, float(row.get("reading_value")), int(row.get("out_of_range") or 0))
		)

	earlier = visit_compare.earlier_visits(doc)
	if earlier:
		wanted = {(feature, key) for key in readings for feature in readings[key]["series"]}
		features = {feature for feature, _key in wanted}
		used = {
			feature: [visit for visit in earlier if visit_compare.covers(visit, feature)][: CHART_POINTS - 1]
			for feature in features
		}
		parents = sorted({visit.name for visits in used.values() for visit in visits})
		history = {}
		if parents:
			for row in frappe.get_all(
				"Sapphire Chemistry Reading",
				filters={"parenttype": DOCTYPE, "parent": ["in", parents]},
				fields=["parent", "reading", "reading_value", "out_of_range", "serial_no"],
			):
				history.setdefault(row.get("parent"), []).append(row)
		for feature, visits in used.items():
			for visit in visits:
				for row in history.get(visit.name, []):
					if not visit_compare.measured(row):
						continue
					row_feature, key = visit_compare.reading_key(visit, row)
					if row_feature != feature or (feature, key) not in wanted:
						continue
					readings[key]["series"][feature].append(
						(visit.day, float(row.get("reading_value")), int(row.get("out_of_range") or 0))
					)

	charts = []
	for key in order:
		chart = readings[key]
		series = {
			feature: sorted(points, key=lambda point: point[0])[-CHART_POINTS:]
			for feature, points in chart["series"].items()
		}
		if max(len(points) for points in series.values()) < 2:
			continue
		labels = {feature: names.get(feature) or feature or "" for feature in series}
		charts.append(svg_chart(chart["title"], chart["uom"], chart["low"], chart["high"], series, labels))
	return charts


def _number(value):
	return f"{float(value):g}"


def svg_chart(title, uom, low, high, series, labels):
	"""A small line chart, print-safe: an inline SVG with every colour from the print design
	system. ``series`` is ``{feature: [(day, value, out_of_range)]}``, oldest first.

	The y axis is labelled at the target range when there is one (what a customer reads the
	line against), else at the lowest and highest value. On a site visit each fountain gets a
	legend row above the plot, so four fountains never cover their own lines."""
	width, left, right, bottom, plot = 330, 44, 12, 28, 102
	legend = list(series) if len(series) > 1 else []
	top = 30 + 13 * len(legend)
	height = top + plot + bottom
	days = sorted({point[0] for points in series.values() for point in points})
	values = [point[1] for points in series.values() for point in points]
	low_f = _bound(low)
	high_f = _bound(high)
	band = [v for v in (low_f, high_f) if v is not None]
	lo, hi = min(values + band), max(values + band)
	if hi == lo:
		hi, lo = hi + 1, lo - 1
	pad = (hi - lo) * 0.12
	lo, hi = lo - pad, hi + pad

	def x(day):
		if len(days) == 1:
			return left + (width - left - right) / 2
		return left + days.index(day) * (width - left - right) / (len(days) - 1)

	def y(value):
		return top + (hi - value) * plot / (hi - lo)

	out = [
		f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
		f'style="font-family:{ps.escape_html(ps.SANS_FONT)};">'
	]
	heading = ps.escape_html(title) + (f" ({ps.escape_html(uom)})" if uom else "")
	out.append(
		f'<text x="{left}" y="14" font-size="12" font-weight="700" fill="{ps.INK_900}">{heading}</text>'
	)
	for index, feature in enumerate(legend):
		colour = SERIES_COLOURS[index % len(SERIES_COLOURS)]
		row = 28 + 13 * index
		out.append(
			f'<line x1="{left}" y1="{row - 3.5}" x2="{left + 14}" y2="{row - 3.5}" stroke="{colour}" stroke-width="2"/>'
			f'<text x="{left + 19}" y="{row}" font-size="9.5" fill="{ps.INK_700}">'
			f"{ps.escape_html(labels.get(feature) or '')}</text>"
		)
	if band:
		band_top = y(high_f if high_f is not None else hi)
		band_bottom = y(low_f if low_f is not None else lo)
		out.append(
			f'<rect x="{left}" y="{band_top:.1f}" width="{width - left - right}" height="{band_bottom - band_top:.1f}" '
			f'fill="{ps.TEAL}" fill-opacity="0.22"/>'
		)
	for value in band or (lo + pad, hi - pad):
		out.append(
			f'<text x="{left - 6}" y="{y(value) + 3.5:.1f}" font-size="10" text-anchor="end" fill="{ps.INK_700}">'
			f"{_number(round(value, 2))}</text>"
		)
	out.append(
		f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" '
		f'stroke="{ps.BORDER_100}" stroke-width="1"/>'
	)
	for day in days:
		out.append(
			f'<text x="{x(day):.1f}" y="{height - bottom + 14}" font-size="9.5" text-anchor="middle" fill="{ps.INK_700}">'
			f"{day.month}/{day.day}</text>"
		)
	for index, points in enumerate(series.values()):
		colour = SERIES_COLOURS[index % len(SERIES_COLOURS)]
		if len(points) > 1:
			path = " ".join(f"{x(day):.1f},{y(value):.1f}" for day, value, _flag in points)
			out.append(f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="2"/>')
		for day, value, flagged in points:
			out.append(
				f'<circle cx="{x(day):.1f}" cy="{y(value):.1f}" r="3.2" fill="{ps.RED if flagged else colour}"/>'
			)
	out.append("</svg>")
	return "".join(out)


def _bound(value):
	"""A target bound, or None: blank and 0 both mean "no bound", as on the reading rows."""
	if value in (None, ""):
		return None
	return float(value) or None


# ---------------------------------------------------------------------- photos


def image_data_uri(file_url, visit):
	"""The image at ``file_url`` as a data URI, shrunk to ``PHOTO_SIDE`` on its longest side, when
	it is a File attached to ``visit``. None for no image, a file attached elsewhere or nowhere,
	or one that cannot be read or shrunk below ``PHOTO_MAX_BYTES``."""
	if not file_url or not visit:
		return None
	name = frappe.db.get_value(
		"File",
		{"file_url": str(file_url), "attached_to_doctype": DOCTYPE, "attached_to_name": visit},
		"name",
		order_by="creation desc",
	)
	if not name:
		return None
	path = frappe.get_doc("File", name).get_full_path()
	if not path or not os.path.isfile(path):
		return None
	with open(path, "rb") as handle:
		raw = handle.read()
	mime = mimetypes.guess_type(path)[0] or "image/jpeg"
	try:
		from PIL import Image, ImageOps

		image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
		image.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
		if image.mode not in ("RGB", "L"):
			image = image.convert("RGB")
		buffer = io.BytesIO()
		image.save(buffer, format="JPEG", quality=80, optimize=True)
		raw, mime = buffer.getvalue(), "image/jpeg"
	except Exception:
		# Pillow could not read it: print it as it is, if a browser can show it at all.
		if not mime.startswith("image/") or len(raw) > PHOTO_MAX_BYTES:
			return None
	return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
