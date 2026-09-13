# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give back the fifteen maintenance contracts the scheduler expired for having no end date.

``maintenance_renewal.expire_or_renew_contracts`` filtered
``{"status": "Active", "end_date": ["<", today]}``. ``end_date`` is nullable and the
field's own description states the invariant::

    "For a fixed year term this is derived from Start Date; the daily scheduler marks
     the contract Expired once it passes. Blank never expires."

Blank is exactly what the filter matched. Frappe wraps a comparison on a nullable
column in an ifnull sentinel set to the *minimum* of the type
(``frappe/model/db_query.py``, ``prepare_filter_condition``), so
``ifnull(end_date, '0001-01-01') < today`` is true for every never-expiring contract.
A blank end date also implies the term is not in ``FIXED_YEAR_TERMS``, so ``renewing``
was False and every one of them fell through to the ``else`` and was force-expired.

**It cost both revenue paths**, because ``status = "Active"`` gates each: visit
scheduling (``tasks.py``, the Active pluck) and recurring billing
(``maintenance_billing.py``). Fifteen contracts stopped producing visits and invoices.

Measured on prod 2026-09-13: fifteen of the site's sixteen contracts — entered by hand
on the 9th and 10th, every one modified on the 11th by ``Administrator``, the scheduler
identity — sitting at ``Expired`` with ``end_date`` NULL. The sixteenth, the only one
carrying a real future end date, was correctly untouched.

--------------------------------------------------------------------------------------
Why restoring to ``Active`` is a revert rather than a guess
--------------------------------------------------------------------------------------

The broken filter selected on ``status = "Active"``. So every row it expired was
**necessarily Active immediately beforehand** — that is not an inference about intent,
it is a property of the query that did the damage. Setting these back to Active restores
the state the sweep found, and nothing more.

The predicate is the writer's own rule: ``status = "Expired"`` **and** ``end_date``
unset. A contract with no end date cannot honestly be expired; the field says so.

``non_renewal_notice = 0`` is ANDed on as the one concession to human intent. If somebody
deliberately ended a month-to-month contract, that flag is how they would have said so,
and this must not resurrect it. All fifteen carry 0.

--------------------------------------------------------------------------------------
Restoring them bills nobody, and that was checked rather than hoped
--------------------------------------------------------------------------------------

The billing sweep requires ``invoicing_frequency in ("Monthly", "Quarterly", "Annually")``
**and** ``recurring_amount > 0``. Fourteen of the fifteen are ``Per Visit``; the one
Monthly contract carries ``recurring_amount = 0``. ``>`` does not match the ifnull
sentinel, so zero and NULL are both excluded. No invoice can be drafted for any of them
by being made Active again.

That safety was luck, not design, which is why the billing filter's own ``is set`` clause
ships in the same release: it stops being luck the moment somebody fills in an amount
before a billing start date.

--------------------------------------------------------------------------------------
Mechanics
--------------------------------------------------------------------------------------

``post_model_sync``. ``db.set_value`` rather than the doc API: ``validate`` re-derives
several fields and this must put the status back and touch nothing else. Safe to run
twice — the second run matches nothing, because the rows it repaired are no longer
``Expired``. Wrapped: a patch that raises aborts ``bench migrate``, which on this repo is
the deploy.
"""

import frappe

CONTRACT = "Sapphire Maintenance Contract"
EXPIRED = "Expired"
ACTIVE = "Active"


def execute():
	try:
		if not frappe.db.exists("DocType", CONTRACT):
			return

		rows = frappe.get_all(
			CONTRACT,
			filters=[
				["status", "=", EXPIRED],
				["end_date", "is", "not set"],
				["non_renewal_notice", "=", 0],
			],
			fields=["name", "customer"],
		)
		if not rows:
			return

		for row in rows:
			frappe.db.set_value(CONTRACT, row["name"], "status", ACTIVE, update_modified=False)

		print(
			"restore_force_expired_maintenance_contracts: restored %d contract(s) to Active -- %s"
			% (len(rows), ", ".join(sorted(r["name"] for r in rows)))
		)
	except Exception:
		frappe.log_error(
			title="restore_force_expired_maintenance_contracts",
			message="Could not restore force-expired maintenance contracts",
		)
