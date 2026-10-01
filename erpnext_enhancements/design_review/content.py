"""A review's content: the sanitized bundle it shows, kept as one private JSON File.

**Why a File and not fields.** v1.570.0 stored each screen in a Long Text field. On save, Frappe
v16 runs ``sanitize_html`` over every such field (``frappe/model/base_document.py``,
``_sanitize_content``), and that cleaner knows nothing about SVG or current CSS: it emptied every
icon's path, dropped every circle's geometry and every ``gap`` from 195 of 195 screens on
production, after this module's own sanitizer had already made them safe. A File's content is
never passed through it. ``design_review.sanitize`` is the one sanitizer, and the sandboxed,
policy-locked frame in the Review Room is the second layer.

**Shape.** The content is the bundle as validated and sanitized by ``importer.import_bundle``,
without its ``ballots`` (those become Design Vote, Verdict and Note records) and with ``parts``
merged append-only across revisions. See ``docs/design-review-bundle.md``.

Reads are cached in redis by content hash: a review's content changes only on import, and the
hash changes with it, so a stale entry can never be served. Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import json
import os

import frappe
from frappe import _

#: Kits a bundle may name instead of shipping its own stylesheet. Append-only, like codes.
KITS = {"sapphire-ux/1": "sapphire_ux_1.css"}
_KIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit")
_CACHE_SECONDS = 24 * 3600


def kit_css(kit: str | None) -> str:
	if not kit:
		return ""
	filename = KITS.get(kit)
	if not filename:
		return ""
	with open(os.path.join(_KIT_DIR, filename), encoding="utf-8") as fh:
		return fh.read()


def load(review: str) -> dict:
	"""The review's content, or ``{}`` when it has none yet. Never re-sanitizes: imports did."""
	row = frappe.db.get_value("Design Review", review, ["content_file", "content_hash"], as_dict=True)
	if not row or not row.content_file:
		return {}
	key = f"ee_design_review_content:{row.content_hash}"
	cached = frappe.cache.get_value(key)
	if cached:
		return cached
	try:
		raw = frappe.get_doc("File", row.content_file).get_content()
	except frappe.DoesNotExistError:
		frappe.throw(_("This review's content file is missing. Import the bundle again."), frappe.ValidationError)
	if isinstance(raw, bytes):
		raw = raw.decode("utf-8")
	content = json.loads(raw)
	frappe.cache.set_value(key, content, expires_in_sec=_CACHE_SECONDS)
	return content


# ---------------------------------------------------------------- lookups over a loaded content


def track(content: dict, track_id: str) -> dict | None:
	return next((t for t in content.get("tracks") or [] if t.get("id") == track_id), None)


def option(content: dict, code: str) -> dict | None:
	return next((o for o in content.get("options") or [] if o.get("code") == code), None)


def options_of(content: dict, track_id: str) -> list[str]:
	return [o["code"] for o in content.get("options") or [] if o.get("track") == track_id]


def votable(content: dict, track_id: str) -> bool:
	t = track(content, track_id)
	return bool(t) and t.get("votable", True) is not False


def has_screen(content: dict, option_code: str, screen_code: str) -> bool:
	return any(
		s.get("option") == option_code and s.get("screen") == screen_code for s in content.get("screens") or []
	)


def part_name(content: dict, track_id: str, screen_code: str, number: int) -> str | None:
	for n, name in (content.get("parts") or {}).get(f"{track_id}:{screen_code}", []):
		if int(n) == int(number):
			return name
	return None
