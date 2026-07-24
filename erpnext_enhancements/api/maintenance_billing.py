"""Recurring maintenance billing (§4.2).

Per-visit contracts invoice on each submitted visit (see
``api.maintenance_workflow.create_sales_invoice``). Monthly/Quarterly/Annually
contracts are billed *in arrears* by :func:`generate_recurring_invoices`: a
daily scheduler job that, when a contract's billing period closes, drafts one
Sales Invoice made of

  * a **base** line — the contract's flat ``recurring_amount`` for the period, and
  * **rolled-up consumables** — the chemicals/materials consumed on the period's
    submitted-but-not-yet-billed visits.

The invoice is left as a **draft** for review; submitting it fires the existing
Sales Invoice ``on_submit`` Stripe auto-charge. Each covered visit's
``sales_invoice`` is stamped so nothing bills twice. One invoice per contract per
run; a contract several periods behind catches up over subsequent runs. Each
contract is billed inside a savepoint so a partial failure rolls back cleanly
(no duplicate base fee).

Gated by "Recurring Maintenance Billing" in ERPNext Enhancements Settings (off
by default), so a migrate bills no one until it is switched on.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, add_years, cint, flt, formatdate, getdate, nowdate

RECURRING_FREQUENCIES = ("Monthly", "Quarterly", "Annually")
PERIODS_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "Annually": 1}


def add_period(day, frequency):
    """The date one billing period after ``day`` for the given cadence, or None."""
    day = getdate(day)
    if frequency == "Monthly":
        return add_months(day, 1)
    if frequency == "Quarterly":
        return add_months(day, 3)
    if frequency == "Annually":
        return add_years(day, 1)
    return None


def generate_recurring_invoices(today=None):
    """Daily: draft one Sales Invoice per due recurring-billing period (§4.2).

    Only Active Monthly/Quarterly/Annually contracts with a positive
    ``recurring_amount`` and an arrived ``next_billing_date`` participate; each is
    billed inside its own savepoint so a failure rolls back and is retried next
    run rather than committing a partial (duplicate-prone) state.
    """
    if not cint(
        frappe.db.get_single_value("ERPNext Enhancements Settings", "recurring_maintenance_billing")
    ):
        return
    today = getdate(today or nowdate())

    for name in frappe.get_all(
        "Sapphire Maintenance Contract",
        filters={
            "status": "Active",
            "invoicing_frequency": ["in", RECURRING_FREQUENCIES],
            "recurring_amount": [">", 0],
            "next_billing_date": ["<=", today],
        },
        pluck="name",
    ):
        frappe.db.savepoint("recurring_billing")
        try:
            _bill_period(name)
        except Exception:
            frappe.db.rollback(save_point="recurring_billing")
            frappe.log_error(frappe.get_traceback(), f"Recurring billing failed: {name}")


def _bill_period(contract_name):
    contract = frappe.get_doc("Sapphire Maintenance Contract", contract_name)
    period_end = getdate(contract.next_billing_date)
    settings = frappe.get_single("ERPNext Enhancements Settings")

    fee_item = _base_fee_item(contract, settings)
    if not fee_item:
        # Hard misconfiguration: recurring_amount > 0 guarantees a charge, so we
        # must never draft an invoice missing the flat base fee, and must NOT
        # advance the period — it retries once a fee item is configured.
        frappe.log_error(
            f"Recurring billing skipped for {contract_name}: no maintenance fee item "
            f"(Sales Order services line or ERPNext Enhancements Settings default).",
            "Recurring billing misconfigured",
        )
        return

    invoice = frappe.new_doc("Sales Invoice")
    invoice.customer = contract.customer
    invoice.project = contract.project
    if contract.project:
        invoice.company = frappe.db.get_value("Project", contract.project, "company") or (
            frappe.defaults.get_global_default("company")
        )
    invoice.posting_date = nowdate()
    invoice.due_date = nowdate()

    # 1. Base recurring line (always present — fee_item is guaranteed above).
    invoice.append(
        "items",
        {
            "item_code": fee_item,
            "qty": 1,
            "rate": flt(contract.recurring_amount),
            "sales_order": contract.sales_order or None,
        },
    )

    # 2. Rolled-up consumables from this period's unbilled submitted visits.
    covered_records = _append_unbilled_consumables(invoice, contract, period_end)

    invoice.set_missing_values()
    invoice.insert()

    for record in covered_records:
        frappe.db.set_value("Sapphire Maintenance Record", record, "sales_invoice", invoice.name)
    _advance_period(contract, period_end)
    contract.add_comment(
        "Comment",
        _("Recurring Sales Invoice {0} drafted for the period ending {1} ({2} visit(s) rolled up).").format(
            frappe.get_link_to_form("Sales Invoice", invoice.name), formatdate(period_end), len(covered_records)
        ),
    )


def _append_unbilled_consumables(invoice, contract, period_end):
    """Append consumable lines from the contract's submitted, un-invoiced visits
    that fall in this period (created on or before ``period_end``).

    Returns the covered Maintenance Record names — all of them, since a visit
    with no billable consumables is still covered by the period's base fee — so
    the caller can stamp their ``sales_invoice`` and never re-bill them.
    """
    records = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={
            "maintenance_contract": contract.name,
            "docstatus": 1,
            "sales_invoice": ["is", "not set"],
            "creation": ["<", add_days(period_end, 1)],
        },
        pluck="name",
    )
    for record in records:
        for row in frappe.get_all(
            "Sapphire Maintenance Consumable",
            filters={"parent": record, "parenttype": "Sapphire Maintenance Record"},
            fields=["item", "qty"],
        ):
            if not row.item or flt(row.qty) <= 0:
                continue
            invoice.append(
                "items",
                {
                    "item_code": row.item,
                    "qty": flt(row.qty),
                    "rate": frappe.db.get_value(
                        "Item Price",
                        {"item_code": row.item, "price_list": "Standard Selling"},
                        "price_list_rate",
                    )
                    or 0,
                },
            )
    return records


def _advance_period(contract, period_end):
    """Advance to the next period. Bumps ``modified`` (default) so a concurrent
    stale form-save is caught rather than silently reverting this write."""
    frappe.db.set_value(
        "Sapphire Maintenance Contract",
        contract.name,
        {"last_billed_on": period_end, "next_billing_date": add_period(period_end, contract.invoicing_frequency)},
    )


def _base_fee_item(contract, settings):
    """The item to hang the base recurring fee on: the linked Sales Order's
    maintenance-fee line item if present, else the configured default fee item."""
    if contract.sales_order and settings.maintenance_services_group:
        so_item = frappe.db.get_value(
            "Sales Order Item",
            {"parent": contract.sales_order, "item_group": settings.maintenance_services_group},
            "item_code",
        )
        if so_item:
            return so_item
    return settings.maintenance_fee_item
