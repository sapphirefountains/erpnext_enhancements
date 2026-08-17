# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``Product Feedback Settings`` — one Single, and the reader every caller uses.

**Read this before touching a default.** A Frappe Single stores one row per field in
``tabSingles``, and a brand-new Single has *no rows at all* until something saves it. So on
the day this ships, ``frappe.db.get_single_value("Product Feedback Settings", anything)``
returns ``None`` on every field, whatever the JSON says the default is — ``load_from_db``
applies no defaults, and the ``new_doc()`` path that would have fires only on a fresh
install. ``CLAUDE.md`` records the version this cost us: adding 37 fields to Chat Settings
made its settings page unsaveable.

Two things follow, and both are load-bearing:

1. **The kill switch is named ``paused``, not ``enabled``.** ``None`` is falsy, so an
   ``enabled`` field would ship the feature dead on arrival and the fix would be a patch
   nobody knew to write. The absent-row state has to *be* the desired state.
2. **Every read goes through :func:`get_settings`, which applies the fallbacks itself.**
   Nothing in this module calls ``get_single_value`` directly, and neither should anything
   else. ``patches/seed_product_feedback_settings.py`` writes the row so the values are
   visible and editable in the desk, but the code does not depend on that patch having run.

``validate()`` clamps and never throws on an empty value, deliberately — a controller that
*rejects* the zeros a never-saved Single presents is exactly what made the Chat Settings
page unsaveable, and the settings that bite are the ones for dormant features, where the
first save is the one you need and the one that fails.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document
from frappe.utils import cint

#: The two dev boards, verified against production on 2026-08-17: PRJ-00580 is
#: "ERPNext Enhancements", PRJ-00755 is "Triton Enhancements". Restated in code as well as
#: in the Single so the feature is correct before the seed patch runs.
DEFAULT_ERPNEXT_PROJECT = "PRJ-00580"
DEFAULT_TRITON_PROJECT = "PRJ-00755"

DEFAULT_MAX_PROPOSED_TASKS = 12
DEFAULT_DUPLICATE_SCAN_LIMIT = 400
DEFAULT_BREAKDOWN_TIMEOUT = 180

#: Upper bounds, applied to whatever a human types as well as to the fallbacks. A proposal
#: longer than this is not reviewed, it is skimmed.
MAX_PROPOSED_TASKS_CEILING = 50
DUPLICATE_SCAN_CEILING = 1000


class ProductFeedbackSettings(Document):
	def validate(self) -> None:
		"""Clamp into range. Never throws — see the module docstring."""
		self.max_proposed_tasks = _clamp(
			self.get("max_proposed_tasks"), DEFAULT_MAX_PROPOSED_TASKS, 1, MAX_PROPOSED_TASKS_CEILING
		)
		self.duplicate_scan_limit = _clamp(
			self.get("duplicate_scan_limit"), DEFAULT_DUPLICATE_SCAN_LIMIT, 0, DUPLICATE_SCAN_CEILING
		)
		self.breakdown_timeout = _clamp(self.get("breakdown_timeout"), DEFAULT_BREAKDOWN_TIMEOUT, 10, 600)


def _clamp(value: Any, fallback: int, low: int, high: int) -> int:
	"""``value`` as an int inside ``[low, high]``, falling back when it is empty."""
	n = cint(value)
	if n <= 0:
		n = fallback
	return max(low, min(high, n))


def get_settings() -> dict[str, Any]:
	"""Effective settings, with every fallback already applied.

	Returns plain values rather than a Document so a background job can hold them without
	a class dependency. Never raises: a site that has this code but has not migrated has no
	``Product Feedback Settings`` DocType at all, and the callers are a website route and a
	worker, both of which would rather run on defaults than 500.
	"""
	row: dict[str, Any] = {}
	try:
		# Uncast on purpose: every value below is coerced here anyway, and an uncast read
		# returns the raw string a never-saved Single would not have written at all.
		row = frappe.db.get_singles_dict("Product Feedback Settings") or {}
	except Exception:
		row = {}

	return {
		"paused": bool(cint(row.get("paused"))),
		"erpnext_project": (row.get("erpnext_project") or "").strip() or DEFAULT_ERPNEXT_PROJECT,
		"triton_project": (row.get("triton_project") or "").strip() or DEFAULT_TRITON_PROJECT,
		"max_proposed_tasks": _clamp(
			row.get("max_proposed_tasks"), DEFAULT_MAX_PROPOSED_TASKS, 1, MAX_PROPOSED_TASKS_CEILING
		),
		"duplicate_scan_limit": _clamp(
			row.get("duplicate_scan_limit"), DEFAULT_DUPLICATE_SCAN_LIMIT, 0, DUPLICATE_SCAN_CEILING
		),
		"breakdown_timeout": _clamp(row.get("breakdown_timeout"), DEFAULT_BREAKDOWN_TIMEOUT, 10, 600),
	}


def allowed_projects() -> tuple[str, str]:
	"""``(erpnext_project, triton_project)`` — the only two Projects work may be written to.

	This pair is the allowlist. A proposed task naming anything else is dropped rather than
	created: the model does not get to choose where work lands.
	"""
	settings = get_settings()
	return settings["erpnext_project"], settings["triton_project"]


def reviewer_users() -> list[str]:
	"""Enabled users on the notification list, de-duplicated, in table order.

	Disabled users are filtered here rather than at send time so a departed employee stops
	receiving mail without anybody remembering to edit the table.
	"""
	try:
		rows = frappe.get_all(
			"Product Feedback Reviewer",
			filters={"parenttype": "Product Feedback Settings"},
			fields=["user"],
			order_by="idx asc",
		)
	except Exception:
		return []

	names = [(r.get("user") or "").strip() for r in rows]
	names = [n for n in names if n]
	if not names:
		return []

	enabled = set(
		frappe.get_all(
			"User",
			filters={"name": ["in", names], "enabled": 1},
			pluck="name",
		)
		or []
	)

	seen: set[str] = set()
	out: list[str] = []
	for n in names:
		if n in enabled and n not in seen:
			seen.add(n)
			out.append(n)
	return out
