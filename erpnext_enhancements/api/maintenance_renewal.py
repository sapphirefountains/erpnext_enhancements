"""Renewal and rate-adjustment engine for Sapphire Maintenance Contracts.

Two daily behaviours, both driven from the maintenance scheduler:

* :func:`expire_or_renew_contracts` — at a contract's End Date, roll the term
  forward one year (§9.2, "successive one-year terms") when auto-renew is
  enabled and no non-renewal notice was given; otherwise expire. Gated globally
  by "Auto-Renew Maintenance Contracts" in ERPNext Enhancements Settings — when
  that flag is off, contracts simply expire (the pre-engine behaviour), so a
  migrate changes nothing until it is switched on. Only **fixed year terms**
  auto-renew; Month-to-Month/Other/blank terms expire at their End Date.

* :func:`send_rate_change_notices` — up to 30 days before a contract's scheduled
  rate effective date (§4.5), alert Accounts once so staff can send the client
  the written notice and update the Sales Order rate. Self-gating: only
  contracts carrying a ``scheduled_rate`` + ``rate_effective_date`` participate.
  This is advisory only — nothing here auto-changes billing.

Notices are internal Notification Logs (staff only); no customer email is sent
from this module. Each per-contract action is isolated so one bad row cannot
abort the daily scheduler run.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_years, cint, flt, fmt_money, formatdate, getdate, nowdate

RENEWAL_LEAD_DAYS = 30  # §4.5 / §9.2 notice window

# Only fixed year terms auto-renew (§9.2 "successive one-year terms"); a
# Month-to-Month agreement has its own 30-day termination (§9.3) and Other/blank
# terms carry no auto-renewal — all of those expire at their End Date instead.
FIXED_YEAR_TERMS = ("One (1) Year", "Two (2) Years")


def expire_or_renew_contracts(today=None):
    """Renew or expire Active contracts whose End Date has passed.

    Called from the daily maintenance scheduler *before* visit generation, so a
    renewed contract keeps generating visits and an expired one stops. Renewal
    rolls End Date forward in whole years until it is in the future (covering a
    contract that lapsed more than a year) and leaves it Active; expiry sets
    ``status`` to Expired. Each row is isolated: a failure on one contract logs
    and continues rather than aborting the run.
    """
    today = getdate(today or nowdate())
    auto_renew_enabled = cint(
        frappe.db.get_single_value("ERPNext Enhancements Settings", "auto_renew_maintenance_contracts")
    )

    for row in frappe.get_all(
        "Sapphire Maintenance Contract",
        filters={"status": "Active", "end_date": ["<", today]},
        fields=["name", "end_date", "auto_renew", "non_renewal_notice", "initial_term", "customer"],
    ):
        try:
            renewing = (
                auto_renew_enabled
                and row.auto_renew
                and not row.non_renewal_notice
                and row.initial_term in FIXED_YEAR_TERMS
            )
            if renewing:
                new_end = getdate(row.end_date)
                while new_end < today:
                    new_end = add_years(new_end, 1)
                # update_modified stays default (True): a stale contract form
                # saved after this run is then caught by check_if_latest instead
                # of silently reverting the renewal.
                frappe.db.set_value(
                    "Sapphire Maintenance Contract",
                    row.name,
                    {"end_date": new_end, "last_renewed_on": today},
                )
                _add_comment(
                    row.name, _("Auto-renewed (§9.2): term extended to {0}.").format(formatdate(new_end))
                )
                _notify(
                    "Projects Manager",
                    _("Maintenance contract auto-renewed: {0} ({1})").format(row.name, row.customer),
                    _(
                        "Contract {0} for {1} reached its term end and auto-renewed for another "
                        "one-year term (§9.2), now ending {2}. If the client intended not to renew, "
                        "set Non-Renewal Notice on the contract and expire it."
                    ).format(row.name, row.customer, formatdate(new_end)),
                    row.name,
                )
            else:
                frappe.db.set_value("Sapphire Maintenance Contract", row.name, "status", "Expired")
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Maintenance expire/renew failed: {row.name}")


def send_rate_change_notices(today=None):
    """Alert Accounts before a scheduled rate change takes effect (§4.5).

    Daily scheduler task. Fires once per scheduled change (stamps
    ``rate_notice_sent`` only after the alert is actually delivered); the
    contract controller clears that stamp when the scheduled rate or effective
    date changes, so a re-scheduled change notifies again. The window is "within
    the next 30 days OR already effective but not yet notified", so a missed or
    back-dated change still surfaces (late) rather than silently never firing.
    Advisory only — staff apply the new rate to the Sales Order.
    """
    today = getdate(today or nowdate())
    window_end = add_days(today, RENEWAL_LEAD_DAYS)
    currency = frappe.defaults.get_global_default("currency")

    for row in frappe.get_all(
        "Sapphire Maintenance Contract",
        filters={
            "status": "Active",
            "scheduled_rate": [">", 0],
            "rate_effective_date": ["<=", window_end],
            "rate_notice_sent": ["is", "not set"],
        },
        fields=["name", "customer", "scheduled_rate", "rate_effective_date"],
    ):
        try:
            delivered = _notify(
                "Accounts Manager",
                _("Maintenance rate change due {0}: {1} ({2})").format(
                    formatdate(row.rate_effective_date), row.name, row.customer
                ),
                _(
                    "Contract {0} for {1} has a scheduled per-visit rate change to {2}, effective {3} "
                    "(§4.5). Send the client the required 30-day written notice and update the linked "
                    "Sales Order rate on or after the effective date."
                ).format(
                    row.name, row.customer,
                    fmt_money(row.scheduled_rate, currency=currency),
                    formatdate(row.rate_effective_date),
                ),
                row.name,
            )
            # Only mark sent once it actually reached someone, so a missing
            # Accounts Manager doesn't permanently suppress the §4.5 notice.
            if delivered:
                frappe.db.set_value("Sapphire Maintenance Contract", row.name, "rate_notice_sent", today)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Maintenance rate notice failed: {row.name}")


def _role_users(role):
    """Enabled System Users holding ``role`` (best-effort)."""
    holders = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
    if not holders:
        return []
    return frappe.get_all(
        "User",
        filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
        pluck="name",
    )


def _notify(role, subject, content, docname):
    """Post a Notification Log to each holder of ``role``; return the count delivered."""
    delivered = 0
    for user in _role_users(role):
        try:
            frappe.get_doc(
                {
                    "doctype": "Notification Log",
                    "subject": subject,
                    "email_content": content,
                    "document_type": "Sapphire Maintenance Contract",
                    "document_name": docname,
                    "for_user": user,
                    "type": "Alert",
                }
            ).insert(ignore_permissions=True)
            delivered += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Maintenance renewal/rate notify failed")
    if not delivered:
        frappe.log_error(f"{subject}: {content}", f"Maintenance notice (no {role} to notify)")
    return delivered


def _add_comment(contract, text):
    try:
        frappe.get_doc("Sapphire Maintenance Contract", contract).add_comment("Comment", text)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Maintenance renewal comment failed")
