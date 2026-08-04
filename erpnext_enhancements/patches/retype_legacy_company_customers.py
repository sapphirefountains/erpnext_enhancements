"""One-time migration patch (post_model_sync; listed in patches.txt).

Retypes the un-migrated legacy import from ``customer_type = "Company"`` to
``"Commercial"``.

## What this is fixing

This site extended ERPNext's stock ``customer_type`` (Company / Individual /
Partnership) with **Commercial** and **Residential**, which is the axis the WP-5
industry rule keys on. The migration was never finished. Measured 2026-08-04:

| ``customer_type`` | Customers | No industry | No group |
|---|---|---|---|
| Commercial | 1,047 | 732 | 705 |
| **Company** | **364** | **364 (100%)** | **364 (100%)** |
| Residential | 185 | 175 | 66 |

Those 364 are *perfectly* anti-correlated with having any data at all. A real
classification does not look like that; an import does.

## The scope is the signature, not the field

This was flagged as the riskier of the two options offered — if any of those 364
were genuinely something other than Commercial, retyping them destroys the
distinction with no record of it. So the WHERE clause is the **full signature**:

    customer_type = 'Company' AND industry IS EMPTY AND customer_group IS EMPTY

A `Company` row carrying any real classification data is therefore never touched.
It stays as it is and shows up in the Account Data Quality report for a human,
which is the correct outcome for a record that does not match the import's
fingerprint.

## It is reversible

Every affected customer id is written to the Error Log before the update, under
"Legacy Company retype", so the exact set can be reconstructed and reverted.
Without that list the change is one-way — ``Company`` and ``Commercial`` are
indistinguishable afterwards.

Idempotent: the filter excludes anything already retyped, so a second run matches
zero rows.
"""

import frappe

#: The type being retired on this site, and its replacement.
LEGACY_TYPE = "Company"
TARGET_TYPE = "Commercial"

#: Chunk size for the id log. A single Error Log row has a practical size limit,
#: and 364 ids is comfortably inside it, but a future re-run on a bigger set
#: should not silently truncate the only record of what changed.
LOG_CHUNK = 200


def execute():
	if not frappe.db.has_column("Customer", "customer_type"):
		return

	affected = _matching_customers()
	if not affected:
		return

	_record_for_reversal(affected)

	# db.sql rather than save(): Customer carries Drive provisioning, contact sync
	# and now the industry gate. Firing all of that several hundred times for a
	# taxonomy correction inside a migration is both slow and a real source of
	# side effects. update_modified is left alone so the change does not look like
	# a human edit in the audit trail.
	frappe.db.sql(
		"""
		UPDATE `tabCustomer`
		SET customer_type = %(target)s
		WHERE customer_type = %(legacy)s
		  AND (industry IS NULL OR industry = '')
		  AND (customer_group IS NULL OR customer_group = '')
		""",
		{"target": TARGET_TYPE, "legacy": LEGACY_TYPE},
	)


def _matching_customers():
	"""Ids matching the full legacy-import signature."""
	return frappe.db.sql_list(
		"""
		SELECT name FROM `tabCustomer`
		WHERE customer_type = %(legacy)s
		  AND (industry IS NULL OR industry = '')
		  AND (customer_group IS NULL OR customer_group = '')
		""",
		{"legacy": LEGACY_TYPE},
	)


def _record_for_reversal(names):
	"""Write the affected ids to the Error Log so the change can be undone.

	Logged BEFORE the update, so a failure part-way through still leaves the
	intended set on record. Error Log is the wrong-sounding home for a
	non-error, but it is the one append-only, searchable store every site has —
	and an unreversible 364-row retype with no record would be the actual error.
	"""
	for start in range(0, len(names), LOG_CHUNK):
		chunk = names[start : start + LOG_CHUNK]
		frappe.log_error(
			message=(
				f"Retyped customer_type {LEGACY_TYPE!r} -> {TARGET_TYPE!r} for "
				f"{len(chunk)} customers (batch {start // LOG_CHUNK + 1}).\n\n"
				"To revert this batch:\n"
				f"  frappe.db.sql(\"UPDATE `tabCustomer` SET customer_type='{LEGACY_TYPE}' \"\n"
				'                 "WHERE name IN (...)", ...)\n\n'
				+ "\n".join(chunk)
			),
			title="Legacy Company retype",
		)
