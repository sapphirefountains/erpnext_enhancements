# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give 22 drifted Single fields the values their JSON has always declared.

--------------------------------------------------------------------------------------
Why this patch is the OPPOSITE of ``backfill_chat_settings_defaults`` — read this first
--------------------------------------------------------------------------------------

That patch fills a field **only when it has no row in ``tabSingles``**, and its docstring
makes the safety argument plainly: *"an unchecked checkbox and a deliberate 0 are both falsy
and are not the same fact, and a patch that 'helpfully' restored defaults over stored zeros
would silently re-enable things somebody switched off."*

That argument is correct, and it is exactly why this patch could not copy the predicate.
**Every one of these 22 fields already HAS a row**, holding the literal string ``'0'`` (or
``NULL`` for the two Data fields). Verified by direct query against production before this
was written, not assumed. So the row-absence predicate matches **zero rows**, commits, and
records itself in ``tabPatch Log`` — which `CLAUDE.md` records as indistinguishable from a
successful run, and which is the v1.280.3 failure exactly.

How the rows came to exist is the documented mechanism, one step further on: saving a Single
through the Desk **deletes and re-inserts every field row** from the in-memory doc, and the
in-memory doc had loaded ``None`` (a Single applies no defaults on load). ``get_valid_dict``
cast that ``None`` to ``cint(None) == 0`` on the way out. So somebody opening the settings
form and pressing Save is what baked the drift to disk. "It self-heals on the next save" is
true of the *row*, and false of the *value* — the save persists the zero.

--------------------------------------------------------------------------------------
What replaces the safety argument
--------------------------------------------------------------------------------------

Not a predicate — an **explicit, enumerated table**, every entry of which was chosen after
reading that field's call sites and deciding what its stored zero actually does.

And one structural guarantee that makes the original worry impossible rather than merely
unlikely: **no ``Check`` field appears in this table.** Twenty are ``Int``, two are ``Float``,
two are ``Data``. A patch that touches no boolean cannot re-enable a feature anyone switched
off, whatever the predicate. ``tests/test_single_default_drift.py`` asserts that, so it stays
true if the table ever grows.

Deliberately **not** here, and each for a stated reason:

* ``briefing_use_gemini`` — the one drifted ``Check``, and a **spend** decision rather than a
  correctness one. Writing 1 starts a Vertex call per recipient every weekday morning where
  there is currently none. It needs a person to say yes, so it is not in this table.
* Every other declared default on these two Singles that is **not** drifted. This is not a
  "restore all defaults" sweep, and must never become one: on a doc whose rows already exist,
  such a sweep can only key on falsiness, and would re-tick ``po_sod_enforcement_enabled``,
  ``handoff_gate_enabled`` and ``fountain_move_auto_convert`` over somebody's decision.
* The other 22 Singles in the app. A cross-Single sweep is the unsafe move; each needs its own
  read-site audit first, which is the whole work this table represents.

--------------------------------------------------------------------------------------
What the zeros were actually doing
--------------------------------------------------------------------------------------

Most of these read through an ``or`` and were inert — wrong data, no behaviour. Eight were
not, because their read site deliberately honours an explicit 0:

* ``pipeline_stale_amber_days`` / ``pipeline_stale_red_days`` — ``_stale_level`` is guarded
  ``if amber_days > 0`` / ``if red_days > 0``, so **all** staleness lighting on the Sales
  Pipeline board is off, while the board's own caption prints the raw value and reads
  "amber after 0 days", the exact inverse of what is happening. 363 of 378 open Opportunities
  are past the declared 7-day threshold.
* ``geofence_radius_m`` — ``api/time_kiosk.py`` treats 0 as a documented *disabled* sentinel
  and returns before any coordinate query, so the nearby-visit suggestion has never worked.
* The five ``fleet_*`` intervals — ``fleet_maintenance/status.py`` uses
  ``cint(raw) if raw not in (None, "") else default``, which is a presence test, not a
  falsiness test, so a stored 0 reaches the engine and ``add_months(last, 0)`` pins a vehicle
  Overdue from the day after it is serviced. Harmless today only because the register is
  empty; it lands on the first vehicle anyone creates.

--------------------------------------------------------------------------------------
Mechanics
--------------------------------------------------------------------------------------

``db.set_single_value``, never ``.save()``: a settings controller's ``validate`` can throw on
a field this patch never touches, and **a patch that raises aborts ``bench migrate``, which on
this repo is the deploy** (v1.395.0 left production half-installed that way).

Never ``frappe.db.get_value("Singles", ...)`` — that table has no ``creation`` column, so the
default ``order_by`` makes the read fail on every site; ``tests/test_singles_table_access.py``
fails the build on it. ``get_single_value`` is the supported door.

Guarded on the DocType existing, wrapped so nothing here can raise, and safe to run twice: the
second run reads a non-falsy value and writes nothing.
"""

import frappe
from frappe.utils import cint

# fieldname -> the value its JSON has always declared.
#
# Kept as strings because that is what tabSingles stores and what the JSON declares; comparing
# like for like is what makes the "already correct" check honest.
ENHANCEMENTS = {
	# --- live: the read site honours an explicit 0 -------------------------------
	"pipeline_stale_amber_days": "45",
	"pipeline_stale_red_days": "90",
	"geofence_radius_m": "250",
	"fleet_weekly_interval_days": "7",
	"fleet_oil_change_interval_months": "3",
	"fleet_dealership_interval_months": "6",
	"fleet_wiper_interval_months": "6",
	"fleet_due_soon_window_days": "7",
	# --- inert today: every read site coalesces, but the stored value is a lie ----
	"kpi_snapshot_retention_days": "120",
	"finance_calendar_max_events": "10",
	"contract_esign_link_expiry_days": "30",
	"fountain_move_max_photo_mb": "10",
	"fountain_move_invite_expiry_days": "60",
	"ai_pending_action_ttl_hours": "1",
	"dunning_retry_days": "2,4,7",
	"wall_rotation_seconds": "60",
	"wall_data_refresh_seconds": "300",
	# --- the coordinate triple, written together or not at all -------------------
	#
	# Half a coordinate pair is worse than none. (40.8894, 0.0) is a point in the
	# Atlantic that renders a real, plausible, wrong forecast; +111.8808 instead of
	# -111.8808 is western China and, being truthy, sails through the `or` guard the
	# zero currently trips. The label rides along so the caption can never name a
	# different place from the one actually queried.
	"weather_latitude": "40.8894",
	"weather_longitude": "-111.8808",
	"weather_label": "Bountiful, UT",
}

TRAINING = {
	# Both inert — `api/training.py` coalesces (`flt(...) or 1.25`), and video.js,
	# blocks.js and progress.py each coalesce again. Written so the settings form
	# stops showing a number that is not the one in force, and so that removing any
	# one of those four `or`s later cannot quietly turn the zeros live.
	"max_playback_rate": "1.25",
	"doc_min_dwell_seconds": "20",
}

TABLE = (
	("ERPNext Enhancements Settings", ENHANCEMENTS),
	("Training Settings", TRAINING),
)


def execute():
	for doctype, fields in TABLE:
		_restore(doctype, fields)


def _restore(doctype, fields):
	if not frappe.db.exists("DocType", doctype):
		# A site that has this code but not yet this doctype. Nothing to do, and
		# certainly nothing worth aborting a migrate over.
		return

	written = []
	for fieldname, want in fields.items():
		try:
			current = frappe.db.get_single_value(doctype, fieldname)
		except Exception:
			# The column may not exist yet on a site mid-migrate. Skip it; the next
			# run picks it up.
			continue

		if not _is_drifted(current):
			# Either already correct, or somebody has since chosen a real value.
			# Both mean leave it alone — this patch restores a default that never
			# arrived, it does not enforce one.
			continue

		try:
			frappe.db.set_single_value(doctype, fieldname, want)
			written.append(fieldname)
		except Exception:
			frappe.log_error(
				title="restore_drifted_single_defaults",
				message="Could not set %s.%s" % (doctype, fieldname),
			)

	if written:
		frappe.clear_document_cache(doctype, doctype)
		print("restore_drifted_single_defaults: %s <- %s" % (doctype, ", ".join(sorted(written))))


def _is_drifted(current):
	"""True when the stored value is the phantom zero this patch exists to replace.

	Falsy covers all three shapes the drift takes: ``None`` (never written), ``""``
	(a Data field saved empty) and ``0`` / ``"0"`` (the cast that Desk saves baked in).

	This is a *falsiness* test, which the Chat Settings patch deliberately refused to
	use — safe here only because of what is in the table above, never because the
	predicate is safe in general. Every entry is an Int, Float or Data dial whose
	declared default is non-zero; none is a Check, so there is no "somebody unticked
	this on purpose" reading of a falsy value to get wrong.
	"""
	if current is None:
		return True
	text = str(current).strip()
	if text == "":
		return True
	try:
		return float(text) == 0.0
	except ValueError:
		# A non-numeric string that is not empty is a real value (a label, a CSV of
		# retry days). Not drifted.
		return False
