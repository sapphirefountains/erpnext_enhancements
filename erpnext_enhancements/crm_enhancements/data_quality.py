# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""WP-5: account data hygiene — the audit, the assisted fixes, and the industry gate.

Every downstream package produces unreliable output against dirty account data.
Measured on production 2026-08-04, of 1,621 Customers:

| Gap | Count |
|---|---|
| No industry | 1,292 |
| No customer group | 1,160 |
| No value stream | 1,118 |

So a per-value-stream dashboard would be reporting on 31% of the book and a
territory-filtered target list would silently miss two thirds of the candidates.
This module is the tooling to close that, plus the gate that stops it reopening.

## `customer_type` is the commercial/residential axis, and it is already dirty

ERPNext ships `customer_type` as Company / Individual / Partnership. This site has
extended it with **Commercial** and **Residential**, which is genuinely useful —
the conditional industry rule has a real field to key on rather than needing a new
one. But the distribution is not clean:

| `customer_type` | Customers | No industry | No group |
|---|---|---|---|
| Commercial | 1,047 | 732 | 705 |
| **Company** | **364** | **364 (100%)** | **364 (100%)** |
| Residential | 185 | 175 | 66 |
| Individual | 13 | 13 | 13 |
| Partnership | 12 | 8 | 12 |

The 364 `Company` rows are *perfectly* anti-correlated with having any data at
all — 100% missing both fields. That is the signature of an un-migrated legacy
import rather than a classification anybody made. ``patches.retype_legacy_company_customers``
moves exactly those to `Commercial`, and is deliberately scoped to the full
signature (type **and** both gaps) so a genuine `Company` carrying real data is
never touched.

## Assisted, never automatic

`bulk_assign` applies a value **a human picked** to rows **a human selected**. It
does not infer industry from a company name, does not pattern-match, and does not
"suggest". That was an explicit requirement and it is the right one: a wrong
industry is invisible once written, and every downstream report then quietly
inherits it. The audit report finds the gaps; a person fills them.

## Why industry is required by a hook and NOT by `reqd` / `mandatory_depends_on`

This was asked for as "make industry required", and it is — but the declarative
form of it would take the QuickBooks sync down, so it is enforced in code instead.

`reqd` and `mandatory_depends_on` are evaluated by `Document._validate_mandatory`
on **every** save, by **every** caller. Two things break if industry becomes
mandatory that way:

1. **The QuickBooks Online sync.** `quickbooks_online/core/mapping.py` creates
   Customers through `_insert_or_manual_review`, which inserts with
   `ignore_permissions=True` but **not** `ignore_mandatory`. QBO payloads carry no
   industry, so every synced customer would fail validation and be parked in
   manual review. QBO is the book of record until the 1 January accounting
   go-live and is explicitly out of bounds without sign-off.
2. **The 732 commercial customers with no industry today.** Editing a phone number
   on any of them would fail with a mandatory-field error about a field the user
   never touched.

`api/telephony.py` and `fountain_move/conversion.py` already set
`flags.ignore_mandatory` on their Customer inserts, so those two would have
survived — but QBO would not, and that is the one that matters.

So enforcement is `enforce_industry` on `validate`, which can tell the cases
apart:

* **honours `doc.flags.ignore_mandatory`** — the same escape hatch frappe's own
  mandatory check uses, so the two integrations that already opted out keep
  working with no change to their code;
* **exempt in background jobs** (`frappe.request` is None) — a scheduled QBO poll
  is not a person who can be asked for an industry, and blocking it converts a
  working sync into a manual-review queue;
* **exempt in bulk contexts** — import/migrate/patch/install/test;
* **new records by default**, because of the 732.

Two toggles in ERPNext Enhancements Settings:

| Setting | Default | Effect |
|---|---|---|
| `require_industry_on_commercial` | **on** | Block a NEW commercial account with no industry |
| `require_industry_on_edit` | off | Also block edits to existing ones |

Turn the second one on **after** the backlog is cleared through the audit report
below — at that point it is safe, and it is what closes the loop for good.
"""

import frappe
from frappe import _
from frappe.utils import cint

#: customer_type values treated as commercial for the industry rule. "Company" is
#: included because it is the legacy spelling of Commercial on this site (see the
#: module docstring); it stays in the list even after the retype patch, so a row
#: created by an old integration is still covered.
COMMERCIAL_TYPES = ("Commercial", "Company", "Partnership")

#: Explicitly NOT commercial. A homeowner should never be forced into an industry.
RESIDENTIAL_TYPES = ("Residential", "Individual")

#: The three fields the audit tracks, in the order a reviewer should fill them.
#: Customer group first: it is the cheapest to decide and the most widely used.
AUDIT_FIELDS = ("customer_group", "industry", "value_stream")

#: Fields `bulk_assign` will write. A closed allowlist — this endpoint takes a
#: fieldname from the client, so the set has to be closed by construction rather
#: than by trusting the caller.
ASSIGNABLE_FIELDS = {
	"customer_group": "Customer Group",
	"industry": "Industry Type",
	"territory": "Territory",
	"custom_account_status": None,  # Select, no link target
	"customer_type": None,
}

#: Roles allowed to bulk-assign. Kept here rather than in Settings so it cannot be
#: widened from the UI.
REVIEWER_ROLES = {"System Manager", "Sales Manager"}


def _settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")


def _flag(fieldname, settings=None):
	settings = settings or _settings()
	return bool(cint(settings.get(fieldname) or 0))


def is_commercial(doc):
	"""True when this account should carry an industry.

	Reads the field defensively — `customer_type` is a stock field, but a doc
	built by a test or an integration may not have set it, and an AttributeError
	inside `validate` would block every Customer save.
	"""
	return (getattr(doc, "customer_type", None) or "") in COMMERCIAL_TYPES


# --------------------------------------------------------------------- the gate


def enforce_industry(doc, method=None):
	"""``Customer.validate``: require an industry on commercial accounts.

	Read the module docstring before changing any of the four guards below —
	each one is there because removing it breaks something specific, and one of
	them (``_is_background``) is what keeps the QuickBooks sync alive.
	"""
	if getattr(doc, "industry", None):
		return
	if not is_commercial(doc):
		# A homeowner has no industry and never will.
		return
	if _bulk_context():
		return
	if _is_background():
		return
	if getattr(doc.flags, "ignore_mandatory", False):
		# The caller explicitly opted out of mandatory validation (telephony's
		# call-log customer, the fountain-move conversion). Honouring the same
		# flag frappe's own check honours means those paths need no change.
		return

	if not _flag("require_industry_on_commercial"):
		return
	if not doc.is_new() and not _flag("require_industry_on_edit"):
		# 732 commercial customers have no industry today. Until that backlog is
		# cleared through the Account Data Quality report, blocking edits would
		# fail a phone-number change with an error about a field nobody touched.
		return

	frappe.throw(
		_("Industry is required for {0} accounts. Pick the closest match, or use 'Other' if none fits.").format(
			getattr(doc, "customer_type", None) or _("commercial")
		),
		title=_("Industry required"),
	)


def _is_background():
	"""True when nothing is driving this save that could be asked for a value.

	A scheduled QuickBooks poll has no ``frappe.request``. Throwing there does not
	prompt anybody — it converts a working sync into a manual-review queue, which
	is precisely the live-critical breakage the QBO module is ring-fenced against.
	"""
	return getattr(frappe, "request", None) is None


def _bulk_context():
	flags = frappe.flags
	return bool(
		flags.in_import
		or flags.in_migrate
		or flags.in_patch
		or flags.in_install
		or flags.in_test
		or flags.in_setup_wizard
	)


# ------------------------------------------------------------- assisted fixes


@frappe.whitelist()
def bulk_assign(customers, fieldname, value):
	"""Apply one human-chosen value to a set of human-selected Customers.

	**Assisted, not automatic.** Nothing here derives a value from anything. The
	caller supplies both the field and the value; this function's whole job is to
	apply them safely to many rows at once so a reviewer working through the audit
	report is not opening 700 forms.

	Returns ``{"updated": n, "skipped": [...]}``. A row already carrying a
	non-empty value in that field is **skipped, not overwritten** — the audit is
	for filling gaps, and a bulk action that silently reassigns existing data is
	how a careful afternoon's work gets undone by one mis-click.
	"""
	if not REVIEWER_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to bulk-assign account data."), frappe.PermissionError)

	if fieldname not in ASSIGNABLE_FIELDS:
		frappe.throw(_("{0} cannot be assigned through this tool.").format(fieldname))

	if isinstance(customers, str):
		customers = frappe.parse_json(customers)
	if not customers:
		return {"updated": 0, "skipped": []}

	value = (value or "").strip()
	if not value:
		frappe.throw(_("Pick a value to assign."))

	# Validate the value ONCE against its link target rather than per row: a typo
	# should fail before it has half-applied across 700 accounts.
	link_target = ASSIGNABLE_FIELDS[fieldname]
	if link_target and not frappe.db.exists(link_target, value):
		frappe.throw(_("{0} '{1}' does not exist.").format(link_target, value))

	updated, skipped = 0, []
	for name in customers[:1000]:
		if not frappe.db.exists("Customer", name):
			skipped.append({"customer": name, "reason": "missing"})
			continue
		if not frappe.has_permission("Customer", "write", doc=name):
			skipped.append({"customer": name, "reason": "no_permission"})
			continue
		if frappe.db.get_value("Customer", name, fieldname):
			skipped.append({"customer": name, "reason": "already_set"})
			continue

		# db.set_value rather than save(): Customer carries Drive provisioning and
		# contact-sync hooks that have no business firing several hundred times for
		# a taxonomy backfill. update_modified stays TRUE here, unlike the
		# migration patches — this is a human editing data, and the audit trail
		# should show it.
		frappe.db.set_value("Customer", name, fieldname, value)
		updated += 1

	return {"updated": updated, "skipped": skipped}


@frappe.whitelist()
def assign_value_streams(customers, value_streams):
	"""Append value streams to the ``custom_value_stream`` Table MultiSelect.

	Separate from ``bulk_assign`` because a Table MultiSelect is not a scalar: the
	operation is "add these", not "set this", and a row may legitimately end up
	with several. Existing rows are preserved and duplicates are skipped.

	Mind the naming trap: the MASTER doctype is ``Value Streams`` (plural, 6 rows)
	and the CHILD TABLE is ``Value Stream`` (singular, ~1,460 rows). They are
	inverted from frappe convention. See ``crm_enhancements/README.md``.
	"""
	if not REVIEWER_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to bulk-assign account data."), frappe.PermissionError)

	if isinstance(customers, str):
		customers = frappe.parse_json(customers)
	if isinstance(value_streams, str):
		value_streams = frappe.parse_json(value_streams)
	if not customers or not value_streams:
		return {"updated": 0, "skipped": []}

	for stream in value_streams:
		if not frappe.db.exists("Value Streams", stream):
			frappe.throw(_("Value Stream '{0}' does not exist.").format(stream))

	updated, skipped = 0, []
	for name in customers[:1000]:
		if not frappe.db.exists("Customer", name):
			skipped.append({"customer": name, "reason": "missing"})
			continue
		if not frappe.has_permission("Customer", "write", doc=name):
			skipped.append({"customer": name, "reason": "no_permission"})
			continue

		# A Table MultiSelect has to go through the document: the child rows carry
		# parent/parenttype/parentfield/idx that a raw insert would have to
		# reproduce by hand, and getting idx wrong corrupts the grid.
		doc = frappe.get_doc("Customer", name)
		existing = {row.value_stream for row in (doc.get("custom_value_stream") or [])}
		added = False
		for stream in value_streams:
			if stream in existing:
				continue
			doc.append("custom_value_stream", {"value_stream": stream})
			added = True

		if not added:
			skipped.append({"customer": name, "reason": "already_set"})
			continue

		doc.save(ignore_permissions=True)
		updated += 1

	return {"updated": updated, "skipped": skipped}


# ------------------------------------------------------------------- the audit


def audit_rows(filters=None):
	"""Customers with at least one of the three gaps, newest activity first.

	Shared by the Account Data Quality report and anything else that wants the
	same definition of "incomplete". Kept here rather than in the report module so
	there is one definition of the gap rather than two that drift.
	"""
	filters = frappe._dict(filters or {})

	conditions = ["c.disabled = 0"]
	values = {}

	if filters.get("customer_type"):
		conditions.append("c.customer_type = %(customer_type)s")
		values["customer_type"] = filters.customer_type
	if filters.get("legacy_only"):
		# The 364-row un-migrated import: Company AND both gaps.
		conditions.append(
			"c.customer_type = 'Company' AND (c.industry IS NULL OR c.industry = '') "
			"AND (c.customer_group IS NULL OR c.customer_group = '')"
		)

	gap_clauses = []
	if filters.get("gap") in (None, "", "Any", "Industry"):
		gap_clauses.append("(c.industry IS NULL OR c.industry = '')")
	if filters.get("gap") in (None, "", "Any", "Customer Group"):
		gap_clauses.append("(c.customer_group IS NULL OR c.customer_group = '')")
	if filters.get("gap") in (None, "", "Any", "Value Stream"):
		gap_clauses.append(
			"NOT EXISTS (SELECT 1 FROM `tabValue Stream` vs "
			"WHERE vs.parent = c.name AND vs.parenttype = 'Customer')"
		)
	if gap_clauses:
		conditions.append("(" + " OR ".join(gap_clauses) + ")")

	# Residential accounts are excluded from the INDUSTRY gap but still audited for
	# group and value stream — forcing a homeowner into an industry is exactly what
	# the conditional rule exists to avoid.
	if filters.get("commercial_only"):
		conditions.append("c.customer_type IN %(commercial)s")
		values["commercial"] = COMMERCIAL_TYPES

	return frappe.db.sql(
		f"""
		SELECT
			c.name,
			c.customer_name,
			c.customer_type,
			c.customer_group,
			c.industry,
			c.territory,
			c.custom_account_status,
			(SELECT GROUP_CONCAT(vs.value_stream ORDER BY vs.idx SEPARATOR ', ')
			   FROM `tabValue Stream` vs
			  WHERE vs.parent = c.name AND vs.parenttype = 'Customer') AS value_streams,
			(SELECT COUNT(*) FROM `tabOpportunity` o WHERE o.party_name = c.name) AS opportunities,
			(SELECT COUNT(*) FROM `tabProject` p WHERE p.customer = c.name) AS projects
		FROM `tabCustomer` c
		WHERE {" AND ".join(conditions)}
		ORDER BY projects DESC, opportunities DESC, c.customer_name ASC
		LIMIT 2000
		""",
		values,
		as_dict=True,
	)
