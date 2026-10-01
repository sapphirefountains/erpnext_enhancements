"""Import a design review from a bundle (WI-079 slice 5).

A **bundle** is one JSON document a concept generator writes. ``scripts/design_review/``
holds the exporter for the artifact-era reviews; any generator can write one. Shape::

	{
	  "format": "sapphire-design-review/1",
	  "title": "Training UX Redesign",
	  "description": "...",
	  "source_url": "https://claude.ai/artifact/...",
	  "stylesheet": "/* the concepts' CSS */",
	  "flow": {"next": {...}, "backto": {...}, "nav": {...}, "text": {...}, "icon": [...]},
	  "tracks": [{"id": "learner", "label": "...", "short": "...", "votable": true,
	              "blurb": "...", "groups": [{"name": "Take the lesson", "screens": ["S03", "S04"]}]}],
	  "options": [{"track": "learner", "code": "L3", "name": "...", "what": "...", "tradeoff": "..."}],
	  "screens": [{"track": "learner", "option": "L3", "screen": "S04", "name": "Lesson",
	               "group": "Take the lesson", "frame": "phone", "w": 390, "h": 844, "html": "<div>..."}],
	  "parts": {"learner:S04": [[1, "Top bar"], [2, "Resume banner"]]},
	  "ballots": {"source_url": "...", "votes": [{"track", "voter", "ranking": ["L3", ...]}],
	              "verdicts": [{"track", "option", "screen", "voter", "verdict"}],
	              "notes": [{"code": "L3-S04-E05", "author": "Name as typed", "text": "..."}]}
	}

What an import does, in order, and why the order matters:

1. **Validate everything first.** Format, tracks, options, screens, parts — and the
   append-only rule (``codes.check_append_only``) against the parts the review already has.
   A revision that renumbers a part is refused **before anything is written**, so a review is
   never half-updated.
2. **Sanitize** every screen and the stylesheet (``sanitize``). What was dropped is counted in
   the import report rather than hidden.
3. **Write** the review, its options, screens and new parts. Screens are replaced per
   (option, screen, frame); options and parts are upserted. Parts are never deleted.
4. **Imported ballots** are stored with ``is_imported = 1``, the name the ballot recorded and
   the ballot's URL. They are counted apart from live votes and are exempt from the participant
   check, because they were cast by proxy on claude.ai before anyone needed an ERPNext login —
   which ADR 0016 §2 allows for history and never for a live review. Re-importing the same
   ballots replaces the earlier copy rather than doubling it.

Runs as the System Manager who imports (``api.design_review.import_review``) or from a
console; ``flags.design_review_import`` is what lets it write the review's content fields.
Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import hashlib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.design_review import codes, sanitize

FORMAT = "sapphire-design-review/1"
FRAMES = ("desk", "phone", "board")
MAX_SCREEN_BYTES = 400_000
MAX_SCREENS = 1500


#: Codes the Review Room writes into its markup and routes, and that element codes are built from.
#: Restricting them here is what makes those uses safe and every note code parseable.
_TRACK_ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9]{0,11}$")


class BundleError(ValueError):
	pass


def validate_bundle(bundle: dict) -> dict:
	"""Shape checks that need no database. Returns a normalised copy; raises ``BundleError``."""
	if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
		raise BundleError(f"This is not a design review bundle (expected format {FORMAT}).")
	title = (bundle.get("title") or "").strip()
	if not title:
		raise BundleError("The bundle has no title.")
	tracks = bundle.get("tracks") or []
	track_ids = [t.get("id") for t in tracks]
	if not tracks or len(set(track_ids)) != len(track_ids) or not all(track_ids):
		raise BundleError("Every track needs a distinct id.")
	bad = [t for t in track_ids if not _TRACK_ID.match(str(t))]
	if bad:
		raise BundleError(f"Track ids are lower case letters, digits and underscores: {bad}.")
	options = bundle.get("options") or []
	option_codes = set()
	for o in options:
		if o.get("track") not in track_ids or not o.get("code") or not o.get("name"):
			raise BundleError(f"Option {o.get('code')!r} needs a known track, a code and a name.")
		if not _CODE.match(str(o["code"])):
			raise BundleError(
				f"Option code {o['code']!r} must be capital letters and digits, starting with a letter, like L3."
			)
		if o["code"] in option_codes:
			raise BundleError(f"Option code {o['code']} appears twice.")
		option_codes.add(o["code"])
	screens = bundle.get("screens") or []
	if not screens or len(screens) > MAX_SCREENS:
		raise BundleError(f"A bundle carries between 1 and {MAX_SCREENS} screens.")
	keys = set()
	for s in screens:
		key = (s.get("option") or "", s.get("screen"), s.get("frame"))
		if s.get("track") not in track_ids or not s.get("screen") or s.get("frame") not in FRAMES:
			raise BundleError(
				f"Screen {key} needs a known track, a screen code and a frame of desk, phone or board."
			)
		if not _CODE.match(str(s.get("screen") or "")):
			raise BundleError(
				f"Screen code {s.get('screen')!r} must be capital letters and digits, like S04."
			)
		if s.get("option") and s["option"] not in option_codes:
			raise BundleError(f"Screen {key} names option {s['option']}, which the bundle does not define.")
		if key in keys:
			raise BundleError(f"Screen {key} appears twice.")
		keys.add(key)
		if len((s.get("html") or "").encode("utf-8")) > MAX_SCREEN_BYTES:
			raise BundleError(f"Screen {key} is over {MAX_SCREEN_BYTES // 1000} KB.")
		if not (40 <= cint(s.get("w")) <= 8000 and 40 <= cint(s.get("h")) <= 8000):
			raise BundleError(f"Screen {key} needs a width and height between 40 and 8000.")
	parts = bundle.get("parts") or {}
	for key, rows in parts.items():
		track, _sep, screen = key.partition(":")
		if track not in track_ids or not _CODE.match(screen):
			raise BundleError(f"Parts key {key!r} must be track:screen for a known track.")
		for row in rows:
			if not (
				isinstance(row, (list, tuple)) and len(row) == 2 and cint(row[0]) > 0 and str(row[1]).strip()
			):
				raise BundleError(f"Parts for {key} must be [number, name] pairs with positive numbers.")
	flow = bundle.get("flow") or {}
	if not isinstance(flow, dict):
		raise BundleError("flow must be an object.")
	return bundle


def import_bundle(bundle: dict, review: str | None = None) -> dict:
	"""Create or update a review from ``bundle``. Returns the import report."""
	bundle = validate_bundle(bundle)
	if review:
		doc = frappe.get_doc("Design Review", review)
	else:
		existing = frappe.get_all(
			"Design Review", filters={"source_url": bundle.get("source_url") or "-"}, pluck="name", limit=1
		)
		doc = frappe.get_doc("Design Review", existing[0]) if existing else frappe.new_doc("Design Review")

	existing_parts: dict[str, list] = {}
	if not doc.is_new():
		for row in frappe.get_all(
			"Design Part",
			filters={"review": doc.name},
			fields=["track", "screen_code", "number", "part_name"],
			limit_page_length=0,
		):
			existing_parts.setdefault(f"{row.track}:{row.screen_code}", []).append(
				[row.number, row.part_name]
			)
	try:
		added_parts = codes.check_append_only(existing_parts, bundle.get("parts") or {})
	except codes.RenumberedPart as exc:
		frappe.throw(str(exc), frappe.ValidationError)

	css, css_dropped = sanitize.sanitize_css(bundle.get("stylesheet") or "")
	dropped_total: dict[str, int] = {}
	clean_screens = []
	for s in bundle["screens"]:
		html, dropped = sanitize.sanitize_html(s.get("html") or "")
		for d in dropped:
			dropped_total[d] = dropped_total.get(d, 0) + 1
		clean_screens.append({**s, "html": html})

	doc.flags.design_review_import = True
	doc.title = bundle["title"].strip()[:140]
	if bundle.get("description") and not doc.description:
		doc.description = bundle["description"]
	doc.source_url = (bundle.get("source_url") or "")[:500]
	doc.stylesheet = css
	doc.flow_rules = json.dumps(bundle.get("flow") or {})
	doc.revision = cint(doc.revision) + 1
	doc.imported_at = now_datetime()
	doc.imported_by = frappe.session.user
	doc.set("tracks", [])
	for t in bundle["tracks"]:
		doc.append(
			"tracks",
			{
				"track_id": t["id"],
				"label": t.get("label") or t["id"],
				"short": t.get("short") or "",
				"votable": 1 if t.get("votable", True) else 0,
				"blurb": t.get("blurb") or "",
				"groups_json": json.dumps(t.get("groups") or []),
			},
		)
	if doc.is_new():
		doc.status = "Draft"
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	name = doc.name

	# options: upsert by code
	have_options = {
		r.option_code: r.name
		for r in frappe.get_all(
			"Design Option", filters={"review": name}, fields=["name", "option_code"], limit_page_length=0
		)
	}
	for i, o in enumerate(bundle.get("options") or []):
		row = (
			frappe.get_doc("Design Option", have_options[o["code"]])
			if o["code"] in have_options
			else frappe.new_doc("Design Option")
		)
		row.update(
			{
				"review": name,
				"track": o["track"],
				"option_code": o["code"],
				"option_name": o["name"][:140],
				"what": o.get("what") or "",
				"tradeoff": o.get("tradeoff") or "",
				"sort_order": i,
			}
		)
		_write(row)

	# screens: replace per (option, screen, frame); delete screens the revision no longer draws
	have_screens = {
		(r.option_code or "", r.screen_code, r.frame): r.name
		for r in frappe.get_all(
			"Design Screen",
			filters={"review": name},
			fields=["name", "option_code", "screen_code", "frame"],
			limit_page_length=0,
		)
	}
	keep = set()
	for i, s in enumerate(clean_screens):
		key = (s.get("option") or "", s["screen"], s["frame"])
		keep.add(key)
		digest = hashlib.sha1(s["html"].encode("utf-8")).hexdigest()
		row = (
			frappe.get_doc("Design Screen", have_screens[key])
			if key in have_screens
			else frappe.new_doc("Design Screen")
		)
		row.update(
			{
				"review": name,
				"track": s["track"],
				"option_code": s.get("option") or "",
				"screen_code": s["screen"],
				"screen_name": (s.get("name") or s["screen"])[:140],
				"group_name": (s.get("group") or "")[:140],
				"frame": s["frame"],
				"width": cint(s["w"]),
				"height": cint(s["h"]),
				"sort_order": i,
				"content_hash": digest,
				"html": s["html"],
			}
		)
		_write(row)
	removed = [have_screens[k] for k in have_screens if k not in keep]
	for screen_name in removed:
		frappe.delete_doc("Design Screen", screen_name, ignore_permissions=True)

	# parts: append only — the check above has already refused any renumbering
	have_part_keys = {f"{key}:{cint(n)}" for key, rows in existing_parts.items() for n, _p in rows}
	for key, rows in (bundle.get("parts") or {}).items():
		track, _sep, screen = key.partition(":")
		for number, part_name in rows:
			if f"{key}:{cint(number)}" in have_part_keys:
				continue
			frappe.get_doc(
				{
					"doctype": "Design Part",
					"review": name,
					"track": track,
					"screen_code": screen,
					"number": cint(number),
					"part_name": str(part_name)[:140],
				}
			).insert(ignore_permissions=True)

	ballots = _import_ballots(name, bundle.get("ballots") or {})

	report = {
		"review": name,
		"revision": doc.revision,
		"options": len(bundle.get("options") or []),
		"screens": len(clean_screens),
		"screens_removed": len(removed),
		"parts_added": len(added_parts),
		"sanitizer_dropped": dropped_total,
		"stylesheet_dropped": len(css_dropped),
		"ballots": ballots,
	}
	frappe.db.set_value(
		"Design Review", name, "import_report", json.dumps(report)[:4000], update_modified=False
	)
	return report


def _import_ballots(review: str, ballots: dict) -> dict:
	"""Store artifact-era votes, verdicts and notes as imported records, replacing an earlier copy."""
	source = (ballots.get("source_url") or "")[:500]
	if not ballots or not source:
		return {}
	for doctype in ("Design Vote", "Design Verdict", "Design Note"):
		for old in frappe.get_all(
			doctype,
			filters={"review": review, "is_imported": 1, "imported_from": source},
			pluck="name",
			limit_page_length=0,
		):
			frappe.delete_doc(doctype, old, ignore_permissions=True)
	tracks = {
		r.option_code: r.track
		for r in frappe.get_all(
			"Design Option", filters={"review": review}, fields=["option_code", "track"], limit_page_length=0
		)
	}
	counts = {"votes": 0, "verdicts": 0, "notes": 0, "skipped": 0}
	for v in ballots.get("votes") or []:
		ranking = [c for c in v.get("ranking") or [] if c in tracks]
		if not ranking or not v.get("voter"):
			counts["skipped"] += 1
			continue
		frappe.get_doc(
			{
				"doctype": "Design Vote",
				"review": review,
				"track": v.get("track") or tracks[ranking[0]],
				"ranking": json.dumps(ranking),
				"is_imported": 1,
				"imported_by_name": str(v["voter"])[:140],
				"imported_from": source,
				"cast_at": v.get("cast_at") or None,
			}
		).insert(ignore_permissions=True)
		counts["votes"] += 1
	for v in ballots.get("verdicts") or []:
		if (
			v.get("option") not in tracks
			or v.get("verdict") not in ("Yes", "Maybe", "No")
			or not v.get("voter")
		):
			counts["skipped"] += 1
			continue
		frappe.get_doc(
			{
				"doctype": "Design Verdict",
				"review": review,
				"track": tracks[v["option"]],
				"option_code": v["option"],
				"screen_code": v.get("screen"),
				"verdict": v["verdict"],
				"is_imported": 1,
				"imported_by_name": str(v["voter"])[:140],
				"imported_from": source,
			}
		).insert(ignore_permissions=True)
		counts["verdicts"] += 1
	for n in ballots.get("notes") or []:
		parsed = codes.parse_code(n.get("code") or "")
		text = (n.get("text") or "").strip()
		if not parsed or not text or parsed[0] not in tracks:
			counts["skipped"] += 1
			continue
		option_code, screen_code, number = parsed
		part_name = frappe.db.get_value(
			"Design Part",
			{"review": review, "track": tracks[option_code], "screen_code": screen_code, "number": number},
			"part_name",
		)
		frappe.get_doc(
			{
				"doctype": "Design Note",
				"review": review,
				"track": tracks[option_code],
				"code": n["code"],
				"option_code": option_code,
				"screen_code": screen_code,
				"part_number": number,
				"part_name": part_name or "",
				"text": text[:2000],
				"status": n.get("status")
				if n.get("status") in ("Open", "Accepted", "Rejected", "Done")
				else "Open",
				"is_imported": 1,
				"imported_by_name": str(n.get("author") or "")[:140],
				"imported_from": source,
			}
		).insert(ignore_permissions=True)
		counts["notes"] += 1
	return counts


def _write(doc) -> None:
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def import_file(file_name: str, review: str | None = None) -> dict:
	"""Import a bundle held as a File (an upload on the Design Review form, or anywhere)."""
	file_doc = frappe.get_doc("File", file_name)
	content = file_doc.get_content()
	if isinstance(content, bytes):
		content = content.decode("utf-8")
	try:
		bundle = json.loads(content)
	except ValueError:
		frappe.throw(_("That file is not JSON."), frappe.ValidationError)
	try:
		return import_bundle(bundle, review=review)
	except BundleError as exc:
		frappe.throw(_(str(exc)), frappe.ValidationError)
