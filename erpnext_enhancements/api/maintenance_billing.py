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

**Billing seasons (v1.624.0).** A seasonal agreement bills only in season: Valley
Fair Mall's reads "service starts May 1st through September 30th, billed monthly",
and before this the job billed its $7,920 base every month of the year. A Monthly
contract with **Bill From** / **Bill Through** months bills a period's base fee when
the period's **middle day** is in those months and on or after the contract's Start
Date (:func:`period_is_billable`). The middle day, because a period runs from billing
date to billing date: one ending 5 May holds five days of May, and billing every
period that touched the season would make six May-to-September invoices out of five
months. A count of in-season days tied at exactly half for a contract billing on the
16th and made six of those too; the middle day gives five for every billing day. The
Start Date stops a contract signed for next season from billing the end of this one
(Valley Fair's first period would have been 9 September to 9 October 2026, for an
agreement that starts on 5 October). Outside the season the base fee is not billed,
but chemicals are always extra, so a visit's consumables still go on a chemicals-only
draft (a Day Dairy winterization in October). Seasons are Monthly only: a quarter is
billed whole or not at all, so a five-month season billed once or twice a year
depending on the month the contract happened to start. The season is independent of
the winter pause (``pause_over_winter``), which moves visits, not bills.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, add_years, cint, flt, formatdate, getdate, nowdate

RECURRING_FREQUENCIES = ("Monthly", "Quarterly", "Annually")
PERIODS_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "Annually": 1}
#: The cadence a billing season applies to (Monthly only; see the module docstring).
SEASONAL_FREQUENCIES = ("Monthly",)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


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


def season_months(start, end):
    """The month numbers (1-12) from ``start`` through ``end``, wrapping the year end.

    None when neither is set: no season, bill all year. The contract's ``validate``
    refuses one without the other, so half a season never reaches here.
    """
    if not (start and end):
        return None
    first, last = MONTHS.index(start) + 1, MONTHS.index(end) + 1
    months = {first}
    month = first
    while month != last:
        month = month % 12 + 1
        months.add(month)
    return months


def billed_periods_per_year(frequency, season_start=None, season_end=None):
    """How many periods a year bill their base fee: a Monthly season's month count, else
    the cadence's periods. The contract divides an Annual Fee by this, so a five-month
    season billed monthly is a fifth of it each time, not a twelfth."""
    months = season_months(season_start, season_end) if frequency in SEASONAL_FREQUENCIES else None
    return len(months) if months else PERIODS_PER_YEAR.get(frequency)


def period_is_billable(contract, period_end):
    """Whether the period ending ``period_end`` bills its base fee (v1.624.0).

    Monthly: when the period's middle day is in the contract's billing season (any month
    without one) and on or after its Start Date. Quarterly and Annually: always.
    """
    if contract.get("invoicing_frequency") not in SEASONAL_FREQUENCIES:
        return True
    period_end = getdate(period_end)
    period_start = getdate(add_months(period_end, -1))
    middle = add_days(period_start, (period_end - period_start).days // 2)
    start_date = contract.get("start_date")
    if start_date and middle < getdate(start_date):
        return False
    months = season_months(contract.get("billing_season_start"), contract.get("billing_season_end"))
    return months is None or middle.month in months


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
        # `["next_billing_date", "is", "set"]` is load-bearing, not tidiness. The field
        # is nullable and its own description says so: "Blank = not yet on recurring
        # billing." Frappe wraps a comparison on a nullable column in an ifnull sentinel
        # set to the MINIMUM of the type, so `ifnull(next_billing_date, '0001-01-01') <=
        # today` matched every contract that had never been put on billing -- and
        # `_bill_period` opens with `getdate(contract.next_billing_date)`, which returns
        # TODAY for None, so it would have drafted an invoice for a period it invented.
        #
        # What kept this off the books was luck rather than design: `recurring_amount >
        # 0` excludes NULL and zero (`>` does not match the sentinel), and every such
        # contract on this site carries 0. It stops being luck the moment somebody fills
        # in an amount before a start date.
        filters=[
            ["status", "=", "Active"],
            ["invoicing_frequency", "in", RECURRING_FREQUENCIES],
            ["recurring_amount", ">", 0],
            ["next_billing_date", "is", "set"],
            ["next_billing_date", "<=", today],
        ],
        pluck="name",
    ):
        frappe.db.savepoint("recurring_billing")
        try:
            _bill_period(name)
        except Exception:
            frappe.db.rollback(save_point="recurring_billing")
            frappe.log_error(title=f"Recurring billing failed: {name}", message=frappe.get_traceback())


def _bill_period(contract_name):
    contract = frappe.get_doc("Sapphire Maintenance Contract", contract_name)
    period_end = getdate(contract.next_billing_date)

    # Before the fee item: a period out of season bills no base fee, so it needs none.
    if not period_is_billable(contract, period_end):
        _bill_out_of_season(contract, period_end)
        return

    settings = frappe.get_single("ERPNext Enhancements Settings")
    fee_item = _base_fee_item(contract, settings)
    if not fee_item:
        # Hard misconfiguration: recurring_amount > 0 guarantees a charge, so we
        # must never draft an invoice missing the flat base fee, and must NOT
        # advance the period — it retries once a fee item is configured.
        frappe.log_error(
            title="Recurring billing misconfigured",
            message=f"Recurring billing skipped for {contract_name}: no maintenance fee item "
            f"(Sales Order services line or ERPNext Enhancements Settings default).",
        )
        return

    invoice = _new_invoice(contract)

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


def _new_invoice(contract):
    invoice = frappe.new_doc("Sales Invoice")
    invoice.customer = contract.customer
    invoice.project = contract.project
    if contract.project:
        invoice.company = frappe.db.get_value("Project", contract.project, "company") or (
            frappe.defaults.get_global_default("company")
        )
    invoice.posting_date = nowdate()
    invoice.due_date = nowdate()
    return invoice


def _bill_out_of_season(contract, period_end):
    """A period outside the billing season: no base fee, but the chemicals used on its
    visits still bill (they are always extra), on a draft of their own. With nothing to
    charge (no chemicals, or none with a selling price) the period is skipped and nothing
    is drafted; its visits stay unbilled and roll into the next invoice like any other."""
    invoice = _new_invoice(contract)
    covered_records = _append_unbilled_consumables(invoice, contract, period_end)
    season = _season_text(contract)
    if any(flt(row.get("rate")) > 0 for row in invoice.get("items") or []):
        invoice.set_missing_values()
        invoice.insert()
        for record in covered_records:
            frappe.db.set_value("Sapphire Maintenance Record", record, "sales_invoice", invoice.name)
        _advance_period(contract, period_end)
        message = _(
            "No base fee for the period ending {0}: {1}. Sales Invoice {2} drafted for the chemicals used on {3} visit(s)."
        ).format(
            formatdate(period_end),
            season,
            frappe.get_link_to_form("Sales Invoice", invoice.name),
            len(covered_records),
        )
    else:
        frappe.db.set_value(
            "Sapphire Maintenance Contract",
            contract.name,
            "next_billing_date",
            add_period(period_end, contract.invoicing_frequency),
        )
        message = _("No invoice for the period ending {0}: {1}.").format(formatdate(period_end), season)
    contract.add_comment("Comment", message)


def _season_text(contract):
    start, end = contract.get("billing_season_start"), contract.get("billing_season_end")
    if start and end:
        return _("its middle day is outside the billing season ({0} to {1}) or before the start date").format(
            _(start), _(end)
        )
    return _("its middle day is before the contract's start date ({0})").format(formatdate(contract.get("start_date")))


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
