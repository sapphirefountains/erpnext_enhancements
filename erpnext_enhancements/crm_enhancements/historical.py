# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Historical Opportunities: deals that predate ERPNext and nobody is working (v1.628.0).

Decision (Nik, 2026-10-13, TASK-2026-00917 / WI-024): historical Opportunities stop raising
"Closed Won but no linked Project" warnings and are left out of KPI measurements. Most of the
won-without-project backlog is Zoho history -- imported 2025-07-14, won in the previous CRM,
delivered long before anybody could have linked a Project here -- and a daily alert or a KPI
that counts it is measuring the migration, not the sales team.

## The rule

An Opportunity is historical when BOTH hold:

* ``creation < CUTOFF`` (2025-08-01). That covers every Zoho import whenever it was won, and
  nothing created in ERPNext after go-live; and
* nobody has worked on it (as below) in the last ``ACTIVITY_DAYS`` (90) days.

"Activity" is any of these, created inside the window. The Comment, ToDo and
Version sources only count when the owner is not Administrator or Guest; a Communication
counts whoever owns it:

* a ``Comment`` on it of type **Comment** or **Attachment** -- somebody wrote a note or
  attached a file. Deliberately NOT ``Info``, ``Edit``, ``Like``, ``Assigned`` and the rest:
  those are written by code as a side effect of something else (an assignment, a save, a
  scheduled job running under whoever triggered it) or are a reaction, and none of them says
  somebody is working the deal. An assignment is counted through its ToDo instead;
* a ``Communication`` on it (an email or a call), attached either through its
  ``reference_doctype``/``reference_name`` or through a ``Communication Link`` timeline row
  (``link_doctype`` "Opportunity"). **Any owner** (lead, 2026-10-13): an email about a deal is
  somebody working it even when the system logged it as Administrator -- on production 4 of
  the 392 Opportunity Communications are, all of them Sent mail. The window is the
  Communication's own ``creation``, not the link row's, so linking an old email to a deal
  today does not make the deal look worked today;
* a ``ToDo`` referencing it (an assignment);
* a ``Version`` on it whose ``changed`` list holds a ``status`` change, and that is not part
  of a Data Import. Every v16 Version carries a ``"data_import": null`` key, so a SQL
  ``NOT LIKE '%data_import%'`` would exclude every row; the JSON is parsed here instead, as
  ``patches/backfill_opportunity_won_date._won_transitions`` does. Bulk edits, imports and
  patches run as Administrator, so the owner test leaves them out as well.

The second half is what lets an old deal come back to life: a comment, an email or a status
change by a person un-flags it on the next nightly run (and a status change by a person
un-flags it immediately -- see :func:`clear_on_person_status_change`). Ninety quiet days later
it is historical again.

## Why a stored field

``Opportunity.custom_is_historical`` is a read-only Check, recomputed nightly by
:func:`refresh_historical_flags`. Number Cards, Dashboard Charts, Report Builder and the list
view can only filter on a stored column, so a Python predicate would have left every one of
them counting the old deals.

## Call sites

Use the helpers rather than spelling the predicate out: :func:`exclude_filters` (a
``get_all`` filters list), :func:`exclude_filter_dict` (dict filters), :func:`exclude_sql`
(raw SQL) and :func:`is_historical` (one document). Every one of them is safe on a database
that does not have the column yet -- doc_events and reports run during ERPNext's own test
bootstrap and on the deploy that first adds the field -- and then excludes nothing.
"""

import json

import frappe

HISTORICAL_FIELD = "custom_is_historical"
CUTOFF = "2025-08-01"
ACTIVITY_DAYS = 90

#: Accounts that are not a person. Administrator is what patches, imports run from the
#: console and most scheduled jobs write as; Guest is a web form or an inbound webhook.
NON_PERSON_OWNERS = ("Administrator", "Guest")

#: The Comment types a person writes on purpose. See the module docstring for the rest.
PERSON_COMMENT_TYPES = ("Comment", "Attachment")

#: Batch size for the ``IN`` lists. ~850 opportunities predate the cutoff; the chunking is
#: there so the rule keeps working if that ever grows, not because it is needed today.
_CHUNK = 500


def has_field():
	"""True when the column exists. Never raises: ``has_column`` raises on an unknown table."""
	try:
		return bool(frappe.db.has_column("Opportunity", HISTORICAL_FIELD))
	except Exception:
		return False


def exclude_filters():
	"""A ``frappe.get_all`` filters LIST fragment that leaves historical deals out.

	Concatenate it onto a list of filters: ``filters=[[...], *exclude_filters()]``. Empty when
	the column does not exist, so it never names a missing field.
	"""
	if not has_field():
		return []
	return [["Opportunity", HISTORICAL_FIELD, "!=", 1]]


def exclude_filter_dict():
	"""The same exclusion for a dict of filters: ``filters={..., **exclude_filter_dict()}``."""
	if not has_field():
		return {}
	return {HISTORICAL_FIELD: ["!=", 1]}


def exclude_sql(alias="o"):
	"""A SQL predicate leaving historical deals out, for a ``WHERE`` clause.

	``alias`` is the Opportunity table's alias in the query; pass ``None`` (or "") for an
	unaliased ``tabOpportunity``. Returns ``1=1`` when the column does not exist, so the query
	still runs on a fresh install. Contains no ``%``, so it is safe inside a query that also
	carries bound parameters.
	"""
	if not has_field():
		return "1=1"
	column = f"{alias}.{HISTORICAL_FIELD}" if alias else HISTORICAL_FIELD
	return f"coalesce({column}, 0) = 0"


def is_historical(doc_or_name):
	"""Whether one Opportunity (a Document, a dict, or a name) is flagged historical."""
	if not doc_or_name:
		return False
	if isinstance(doc_or_name, str):
		if not has_field():
			return False
		try:
			value = frappe.db.get_value("Opportunity", doc_or_name, HISTORICAL_FIELD)
		except Exception:
			return False
		return bool(frappe.utils.cint(value))
	getter = getattr(doc_or_name, "get", None)
	value = getter(HISTORICAL_FIELD) if callable(getter) else getattr(doc_or_name, HISTORICAL_FIELD, None)
	return bool(frappe.utils.cint(value))


# ---------------------------------------------------------------------------
# The activity test
# ---------------------------------------------------------------------------


def _chunks(names):
	names = list(names)
	for start in range(0, len(names), _CHUNK):
		yield names[start : start + _CHUNK]


def _is_status_change(data):
	"""A Version's JSON records a status change and did not come from a Data Import."""
	try:
		payload = json.loads(data or "{}") or {}
	except (ValueError, TypeError):
		return False
	if not isinstance(payload, dict):
		return False
	# The key is present on every v16 Version, with null when no import was involved, so
	# the VALUE is what is tested -- never the key's presence.
	if payload.get("data_import"):
		return False
	for change in payload.get("changed") or []:
		# ["fieldname", old, new] -- free-form JSON, so guard the shape.
		if isinstance(change, (list, tuple)) and len(change) >= 3 and change[0] == "status":
			return True
	return False


def active_recently(names, since):
	"""The subset of ``names`` (Opportunity names) a person has worked on since ``since``.

	A handful of queries per chunk of names -- one per source (two for the Communication
	Link path: the link rows, then their Communications' dates), each an ``IN`` list --
	never one per opportunity. See the module docstring for what counts and why.
	"""
	active = set()
	not_a_person = ["not in", list(NON_PERSON_OWNERS)]
	for chunk in _chunks(n for n in names if n):
		active.update(
			frappe.get_all(
				"Comment",
				filters=[
					["reference_doctype", "=", "Opportunity"],
					["reference_name", "in", chunk],
					["comment_type", "in", list(PERSON_COMMENT_TYPES)],
					["creation", ">=", since],
					["owner", *not_a_person],
				],
				pluck="reference_name",
				limit_page_length=0,
			)
		)
		# Communications count whoever owns them (see the module docstring), so no owner
		# filter on either path.
		active.update(
			frappe.get_all(
				"Communication",
				filters=[
					["reference_doctype", "=", "Opportunity"],
					["reference_name", "in", chunk],
					["creation", ">=", since],
				],
				pluck="reference_name",
				limit_page_length=0,
			)
		)
		links = frappe.get_all(
			"Communication Link",
			filters=[
				["parenttype", "=", "Communication"],
				["link_doctype", "=", "Opportunity"],
				["link_name", "in", chunk],
			],
			fields=["parent", "link_name"],
			limit_page_length=0,
		)
		if links:
			recent = set(
				frappe.get_all(
					"Communication",
					filters=[
						["name", "in", list({link.get("parent") for link in links})],
						["creation", ">=", since],
					],
					pluck="name",
					limit_page_length=0,
				)
			)
			active.update(link.get("link_name") for link in links if link.get("parent") in recent)
		active.update(
			frappe.get_all(
				"ToDo",
				filters=[
					["reference_type", "=", "Opportunity"],
					["reference_name", "in", chunk],
					["creation", ">=", since],
					["owner", *not_a_person],
				],
				pluck="reference_name",
				limit_page_length=0,
			)
		)
		# Only the rows that are not already known to be active need their JSON read.
		pending = [name for name in chunk if name not in active]
		if not pending:
			continue
		for version in frappe.get_all(
			"Version",
			filters=[
				["ref_doctype", "=", "Opportunity"],
				["docname", "in", pending],
				["creation", ">=", since],
				["owner", *not_a_person],
			],
			fields=["docname", "data"],
			limit_page_length=0,
		):
			if version.get("docname") not in active and _is_status_change(version.get("data")):
				active.add(version.get("docname"))
	return {name for name in active if name}


# ---------------------------------------------------------------------------
# The nightly recompute
# ---------------------------------------------------------------------------


def _write(names, value):
	"""One UPDATE per chunk. ``update_modified=False``: ``modified`` is itself a signal
	several reports read (stalled deals, the win-rate's Lost leg), and a nightly flag must
	not make 850 old deals look touched today."""
	for chunk in _chunks(names):
		frappe.db.set_value(
			"Opportunity", {"name": ["in", chunk]}, HISTORICAL_FIELD, value, update_modified=False
		)


def refresh_historical_flags():
	"""Daily scheduler (hooks.py) and the v1.628.0 patch: recompute the flag for every deal.

	Flags each Opportunity created before :data:`CUTOFF` with no person's activity in the
	last :data:`ACTIVITY_DAYS` days, un-flags the rest, and clears the flag on anything
	created on or after the cutoff (which can only carry it if somebody set it by hand).
	Writes only the rows whose value changes. Never raises: a failed run logs and leaves
	yesterday's flags, which is the right failure for a reporting filter.

	Returns ``{"flagged", "unflagged", "unchanged"}`` (plus ``"skipped"`` or ``"error"``).
	"""
	counts = {"flagged": 0, "unflagged": 0, "unchanged": 0}
	if not has_field():
		counts["skipped"] = f"Opportunity.{HISTORICAL_FIELD} does not exist yet."
		return counts
	try:
		since = frappe.utils.add_days(frappe.utils.now_datetime(), -ACTIVITY_DAYS)
		old = frappe.get_all(
			"Opportunity",
			filters=[["creation", "<", CUTOFF]],
			fields=["name", HISTORICAL_FIELD],
			limit_page_length=0,
		)
		active = active_recently([row.get("name") for row in old], since)

		to_flag, to_unflag = [], []
		for row in old:
			want = row.get("name") not in active
			have = bool(frappe.utils.cint(row.get(HISTORICAL_FIELD)))
			if want == have:
				counts["unchanged"] += 1
			elif want:
				to_flag.append(row.get("name"))
			else:
				to_unflag.append(row.get("name"))

		# Nothing created after go-live is historical. Only reachable through a hand edit or
		# an API write, since the field is read-only on the form.
		to_unflag.extend(
			frappe.get_all(
				"Opportunity",
				filters=[["creation", ">=", CUTOFF], [HISTORICAL_FIELD, "=", 1]],
				pluck="name",
				limit_page_length=0,
			)
		)

		_write(to_flag, 1)
		_write(to_unflag, 0)
		counts["flagged"] = len(to_flag)
		counts["unflagged"] = len(to_unflag)
	except Exception:
		counts["error"] = True
		frappe.log_error(
			title="Historical opportunity refresh failed",
			message=frappe.get_traceback(),
			reference_doctype="Opportunity",
		)
		return counts

	frappe.logger().info(
		"Historical opportunities: {flagged} flagged, {unflagged} un-flagged, {unchanged} unchanged.".format(
			**counts
		)
	)
	return counts


# ---------------------------------------------------------------------------
# Opportunity before_save
# ---------------------------------------------------------------------------


def clear_on_person_status_change(doc, method=None):
	"""Opportunity ``before_save``: a person moving a historical deal's status revives it.

	Without this the deal would stay excluded until the next nightly run -- long enough for
	somebody who just marked an old deal Closed Won to be told nothing is waiting on a
	project. Before_save rather than on_update, so it rides the save already happening.

	Only for a person: Administrator, a patch, a data import or a migrate changing status is
	exactly the kind of bulk edit the rule exists to ignore.
	"""
	if not frappe.utils.cint(doc.get(HISTORICAL_FIELD)) or doc.is_new():
		return
	if frappe.session.user in NON_PERSON_OWNERS:
		return
	flags = frappe.flags
	if (
		getattr(flags, "in_import", False)
		or getattr(flags, "in_patch", False)
		or getattr(flags, "in_migrate", False)
	):
		return
	if doc.has_value_changed("status"):
		doc.set(HISTORICAL_FIELD, 0)
