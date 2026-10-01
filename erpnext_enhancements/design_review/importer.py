"""Import a design review from a bundle (WI-079 slice 5).

A **bundle** is one JSON document, ``"format": "sapphire-design-review/1"``, that any generator
can write — a script, Claude, Triton. ``docs/design-review-bundle.md`` is the authoring guide,
``design_review/bundle.schema.json`` the machine-readable shape, and
``design_review/examples/minimal-bundle.json`` a complete small example. Three ways in, all
landing here: the Review Room's import button, ``api.design_review.import_review``, and the
``submit_design_review`` assistant tool (gated, so a person confirms it).

What an import does, in order, and why the order matters:

1. **Validate everything first** (``validate_bundle``): format, codes, sizes, references, and
   the append-only rule (``codes.check_append_only``) against the parts the review already
   has. A revision that renumbers a part is refused **before anything is written**.
2. **Sanitize** every screen and the stylesheet (``sanitize``). What was dropped is counted in
   the report rather than hidden.
3. **Write** the review and one private JSON File holding the sanitized content (``content``
   explains why a File). The previous revision's file is removed once the new one is in place.
4. **Imported ballots** become Design Vote, Verdict and Note records with ``is_imported = 1``,
   counted apart from live ones and exempt from the participant check: they were cast by proxy
   on claude.ai before anyone needed an ERPNext login, which ADR 0016 §2 allows for history only.
   Re-importing the same ballots replaces the earlier copy.

Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import hashlib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.design_review import codes, content, sanitize

FORMAT = "sapphire-design-review/1"
FRAMES = ("desk", "phone", "board")
MAX_SCREEN_BYTES = 400_000
MAX_SCREENS = 1500
MAX_BUNDLE_BYTES = 12_000_000

#: Codes the Review Room puts in routes and builds element codes from. Restricting them here is
#: what makes those uses safe and every note code parseable.
_TRACK_ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9]{0,11}$")
_TARGET = re.compile(r"^(?:[a-z][a-z0-9_]{0,30}:)?[A-Z][A-Z0-9]{0,11}$|^(?:next|up|end|\+1|-1)$|^done:.{1,300}$")


class BundleError(ValueError):
	pass


def validate_bundle(bundle: dict) -> dict:
	"""Shape checks that need no database. Raises ``BundleError`` listing what is wrong."""
	if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
		raise BundleError(f"This is not a design review bundle (expected \"format\": \"{FORMAT}\").")
	if not (bundle.get("title") or "").strip():
		raise BundleError("The bundle has no title.")
	if bundle.get("kit") and bundle["kit"] not in content.KITS:
		raise BundleError(f"Unknown kit {bundle['kit']!r}. Known kits: {', '.join(content.KITS)}.")
	tracks = bundle.get("tracks") or []
	track_ids = [t.get("id") for t in tracks]
	if not tracks or len(set(track_ids)) != len(track_ids) or not all(track_ids):
		raise BundleError("Every track needs a distinct id.")
	bad = [t for t in track_ids if not _TRACK_ID.match(str(t))]
	if bad:
		raise BundleError(f"Track ids are lower case letters, digits and underscores: {bad}.")
	for t in tracks:
		for g in t.get("groups") or []:
			if not isinstance(g.get("screens"), list):
				raise BundleError(f"Track {t['id']}: every group needs a list of screen codes.")
	options = bundle.get("options") or []
	option_codes = set()
	for o in options:
		if o.get("track") not in track_ids or not o.get("code") or not o.get("name"):
			raise BundleError(f"Option {o.get('code')!r} needs a known track, a code and a name.")
		if not _CODE.match(str(o["code"])):
			raise BundleError(f"Option code {o['code']!r} must be capital letters and digits, starting with a letter, like L3.")
		if o["code"] in option_codes:
			raise BundleError(f"Option code {o['code']} appears twice; option codes are unique across tracks.")
		option_codes.add(o["code"])
	screens = bundle.get("screens") or []
	if not screens or len(screens) > MAX_SCREENS:
		raise BundleError(f"A bundle carries between 1 and {MAX_SCREENS} screens.")
	keys = set()
	for s in screens:
		key = (s.get("option"), s.get("screen"), s.get("frame"))
		if s.get("track") not in track_ids or s.get("frame") not in FRAMES:
			raise BundleError(f"Screen {key} needs a known track and a frame of desk, phone or board.")
		if not _CODE.match(str(s.get("screen") or "")):
			raise BundleError(f"Screen code {s.get('screen')!r} must be capital letters and digits, like S04.")
		if s.get("option") not in option_codes:
			raise BundleError(f"Screen {key} names option {s.get('option')!r}, which the bundle does not define.")
		owner = next(o for o in options if o["code"] == s["option"])
		if owner["track"] != s["track"]:
			raise BundleError(f"Screen {key} is in track {s['track']} but its option is in {owner['track']}.")
		if key in keys:
			raise BundleError(f"Screen {key} appears twice.")
		keys.add(key)
		if len((s.get("html") or "").encode("utf-8")) > MAX_SCREEN_BYTES:
			raise BundleError(f"Screen {key} is over {MAX_SCREEN_BYTES // 1000} KB.")
		if not (40 <= cint(s.get("w")) <= 8000 and 40 <= cint(s.get("h")) <= 8000):
			raise BundleError(f"Screen {key} needs a width and height between 40 and 8000.")
	for key, rows in (bundle.get("parts") or {}).items():
		track_id, _sep, screen = key.partition(":")
		if track_id not in track_ids or not _CODE.match(screen):
			raise BundleError(f"Parts key {key!r} must be track:screen for a known track.")
		for row in rows:
			if not (isinstance(row, (list, tuple)) and len(row) == 2 and cint(row[0]) > 0 and str(row[1]).strip()):
				raise BundleError(f"Parts for {key} must be [number, name] pairs with positive numbers.")
	_validate_flow(bundle.get("flow") or {})
	return bundle


def _validate_flow(flow: dict) -> None:
	if not isinstance(flow, dict):
		raise BundleError("flow must be an object.")
	problems = []

	def target(where, value):
		if value is None:
			return
		if not isinstance(value, str) or not _TARGET.match(value):
			problems.append(f"{where}: {value!r} is not a target (a screen code, track:screen, next, up, end or done:<message>).")

	def pattern(where, value):
		try:
			re.compile(str(value))
		except re.error as exc:
			problems.append(f"{where}: {value!r} is not a regular expression ({exc}).")

	for key in ("next", "backto"):
		for track_id, table in (flow.get(key) or {}).items():
			for screen, value in (table or {}).items():
				target(f"flow.{key}.{track_id}.{screen}", value)
	for key, value in (flow.get("chain") or {}).items():
		target(f"flow.chain.{key}", value)
	for track_id, rules in (flow.get("text") or {}).items():
		for i, rule in enumerate(rules or []):
			pattern(f"flow.text.{track_id}[{i}]", rule[0])
			target(f"flow.text.{track_id}[{i}]", rule[1])
	for key in ("icon", "aria"):
		for i, rule in enumerate(flow.get(key) or []):
			pattern(f"flow.{key}[{i}]", rule[0])
			target(f"flow.{key}[{i}]", rule[1])
	for track_id, table in (flow.get("nav") or {}).items():
		for part, rule in (table or {}).items():
			if isinstance(rule, dict):
				for screen, value in rule.items():
					target(f"flow.nav.{track_id}.{part}.{screen}", value)
			elif isinstance(rule, list):
				for i, pair in enumerate(rule):
					pattern(f"flow.nav.{track_id}.{part}[{i}]", pair[0])
					target(f"flow.nav.{track_id}.{part}[{i}]", pair[1])
			else:
				target(f"flow.nav.{track_id}.{part}", rule)
	for key, value in (flow.get("say") or {}).items():
		if not isinstance(value, str) or len(value) > 300:
			problems.append(f"flow.say.{key}: narration is a sentence of up to 300 characters.")
	if problems:
		raise BundleError("The click-through rules have problems:\n- " + "\n- ".join(problems[:40]))


def check_bundle(bundle: dict, review: str | None = None) -> dict:
	"""A dry run: everything an import checks, nothing written. For the assistant tool and the UI."""
	validate_bundle(bundle)
	existing = (content.load(review) if review else {}).get("parts") or {}
	added = codes.check_append_only(existing, bundle.get("parts") or {})
	dropped: dict[str, int] = {}
	for s in bundle["screens"]:
		for d in sanitize.sanitize_html(s.get("html") or "")[1]:
			dropped[d] = dropped.get(d, 0) + 1
	_css, css_dropped = sanitize.sanitize_css(bundle.get("stylesheet") or "")
	return {
		"ok": True,
		"tracks": len(bundle["tracks"]),
		"options": len(bundle.get("options") or []),
		"screens": len(bundle["screens"]),
		"parts_added": len(added),
		"sanitizer_dropped": dropped,
		"stylesheet_dropped": css_dropped,
		"ballots": {k: len((bundle.get("ballots") or {}).get(k) or []) for k in ("votes", "verdicts", "notes")},
	}


def import_bundle(bundle: dict, review: str | None = None) -> dict:
	"""Create or update a review from ``bundle``. Returns the import report."""
	validate_bundle(bundle)
	if review:
		doc = frappe.get_doc("Design Review", review)
	else:
		existing = frappe.get_all("Design Review", filters={"source_url": bundle.get("source_url") or "-"}, pluck="name", limit=1)
		doc = frappe.get_doc("Design Review", existing[0]) if existing else frappe.new_doc("Design Review")

	before = {} if doc.is_new() else content.load(doc.name)
	existing_parts = before.get("parts") or {}
	try:
		added_parts = codes.check_append_only(existing_parts, bundle.get("parts") or {})
	except codes.RenumberedPart as exc:
		frappe.throw(str(exc), frappe.ValidationError)

	merged_parts = {key: [list(r) for r in rows] for key, rows in existing_parts.items()}
	for key, rows in (bundle.get("parts") or {}).items():
		have = {int(n) for n, _p in merged_parts.get(key, [])}
		merged_parts.setdefault(key, []).extend([int(n), str(p)] for n, p in rows if int(n) not in have)
		merged_parts[key].sort(key=lambda r: r[0])

	dropped_total: dict[str, int] = {}
	screens = []
	for s in bundle["screens"]:
		html, dropped = sanitize.sanitize_html(s.get("html") or "")
		for d in dropped:
			dropped_total[d] = dropped_total.get(d, 0) + 1
		screens.append({
			"track": s["track"], "option": s["option"], "screen": s["screen"],
			"name": str(s.get("name") or s["screen"])[:140], "group": str(s.get("group") or "")[:140],
			"frame": s["frame"], "w": cint(s["w"]), "h": cint(s["h"]), "html": html,
		})
	css, css_dropped = sanitize.sanitize_css(bundle.get("stylesheet") or "")
	payload = {
		"format": FORMAT,
		"title": bundle["title"].strip()[:140],
		"description": str(bundle.get("description") or "")[:2000],
		"source_url": str(bundle.get("source_url") or "")[:500],
		"kit": bundle.get("kit") or None,
		"stylesheet": css,
		"flow": bundle.get("flow") or {},
		"tracks": [{
			"id": t["id"], "label": str(t.get("label") or t["id"])[:140], "short": str(t.get("short") or "")[:40],
			"votable": t.get("votable", True) is not False, "blurb": str(t.get("blurb") or "")[:1000],
			"groups": [{"name": str(g.get("name") or "")[:140], "screens": [str(x) for x in g.get("screens") or []]} for g in t.get("groups") or []],
		} for t in bundle["tracks"]],
		"options": [{
			"track": o["track"], "code": o["code"], "name": str(o["name"])[:140],
			"what": str(o.get("what") or "")[:2000], "tradeoff": str(o.get("tradeoff") or "")[:2000],
		} for o in bundle.get("options") or []],
		"screens": screens,
		"parts": merged_parts,
	}
	text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
	digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]

	doc.title = payload["title"]
	if payload["description"] and not doc.description:
		doc.description = payload["description"]
	if doc.is_new():
		doc.status = "Draft"
		doc.flags.design_review_import = True
		doc.insert(ignore_permissions=True)
	old_file = doc.content_file

	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": f"design-review-{doc.name}-r{cint(doc.revision) + 1}.json",
		"is_private": 1,
		"attached_to_doctype": "Design Review",
		"attached_to_name": doc.name,
		"content": text,
	}).insert(ignore_permissions=True)

	doc.flags.design_review_import = True
	doc.revision = cint(doc.revision) + 1
	doc.source_url = payload["source_url"]
	doc.content_file = file_doc.name
	doc.content_hash = digest
	doc.imported_at = now_datetime()
	doc.imported_by = frappe.session.user
	doc.save(ignore_permissions=True)
	if old_file and old_file != file_doc.name and frappe.db.exists("File", old_file):
		frappe.delete_doc("File", old_file, ignore_permissions=True)

	ballots = _import_ballots(doc.name, payload, bundle.get("ballots") or {})
	report = {
		"review": doc.name,
		"revision": doc.revision,
		"tracks": len(payload["tracks"]),
		"options": len(payload["options"]),
		"screens": len(screens),
		"parts_added": len(added_parts),
		"sanitizer_dropped": dropped_total,
		"stylesheet_dropped": len(css_dropped),
		"ballots": ballots,
	}
	frappe.db.set_value("Design Review", doc.name, "import_report", json.dumps(report)[:4000], update_modified=False)
	return report


def _import_ballots(review: str, payload: dict, ballots: dict) -> dict:
	"""Artifact-era votes, verdicts and notes as imported records, replacing an earlier copy."""
	source = str(ballots.get("source_url") or "")[:500]
	if not ballots or not source:
		return {}
	for doctype in ("Design Vote", "Design Verdict", "Design Note"):
		for old in frappe.get_all(doctype, filters={"review": review, "is_imported": 1, "imported_from": source}, pluck="name", limit_page_length=0):
			frappe.delete_doc(doctype, old, ignore_permissions=True)
	track_of = {o["code"]: o["track"] for o in payload["options"]}
	counts = {"votes": 0, "verdicts": 0, "notes": 0, "skipped": 0}
	for v in ballots.get("votes") or []:
		ranking = [c for c in v.get("ranking") or [] if c in track_of]
		if not ranking or not v.get("voter"):
			counts["skipped"] += 1
			continue
		frappe.get_doc({"doctype": "Design Vote", "review": review, "track": track_of[ranking[0]], "ranking": json.dumps(ranking),
		                "is_imported": 1, "imported_by_name": str(v["voter"])[:140], "imported_from": source,
		                "cast_at": v.get("cast_at") or None}).insert(ignore_permissions=True)
		counts["votes"] += 1
	for v in ballots.get("verdicts") or []:
		if v.get("option") not in track_of or v.get("verdict") not in ("Yes", "Maybe", "No") or not v.get("voter"):
			counts["skipped"] += 1
			continue
		frappe.get_doc({"doctype": "Design Verdict", "review": review, "track": track_of[v["option"]], "option_code": v["option"],
		                "screen_code": v.get("screen"), "verdict": v["verdict"], "is_imported": 1,
		                "imported_by_name": str(v["voter"])[:140], "imported_from": source}).insert(ignore_permissions=True)
		counts["verdicts"] += 1
	for n in ballots.get("notes") or []:
		parsed = codes.parse_code(n.get("code") or "")
		text = (n.get("text") or "").strip()
		if not parsed or not text or parsed[0] not in track_of:
			counts["skipped"] += 1
			continue
		option_code, screen_code, number = parsed
		status = n.get("status") if n.get("status") in ("Open", "Accepted", "Rejected", "Done") else "Open"
		frappe.get_doc({"doctype": "Design Note", "review": review, "track": track_of[option_code], "code": n["code"],
		                "option_code": option_code, "screen_code": screen_code, "part_number": number,
		                "part_name": content.part_name(payload, track_of[option_code], screen_code, number) or "",
		                "text": text[:2000], "status": status, "is_imported": 1,
		                "imported_by_name": str(n.get("author") or "")[:140], "imported_from": source}).insert(ignore_permissions=True)
		counts["notes"] += 1
	return counts


def parse_bundle_text(text: str) -> dict:
	"""JSON text to a bundle, with the size limit and a readable error."""
	if isinstance(text, bytes):
		text = text.decode("utf-8")
	if len(text.encode("utf-8")) > MAX_BUNDLE_BYTES:
		raise BundleError(f"A bundle is at most {MAX_BUNDLE_BYTES // 1_000_000} MB.")
	try:
		bundle = json.loads(text)
	except ValueError as exc:
		raise BundleError(f"That is not JSON: {exc}.") from None
	if not isinstance(bundle, dict):
		raise BundleError("A bundle is a JSON object.")
	return bundle


def import_file(file_name: str, review: str | None = None) -> dict:
	"""Import a bundle held as a File (uploaded from the Review Room, or anywhere)."""
	try:
		return import_bundle(parse_bundle_text(frappe.get_doc("File", file_name).get_content()), review=review)
	except BundleError as exc:
		frappe.throw(_(str(exc)), frappe.ValidationError)
