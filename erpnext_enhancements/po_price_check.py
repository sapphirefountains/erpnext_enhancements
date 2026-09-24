# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Warn, never block, when a Purchase Order is submitted with a $0 line (POL-0602 §4.6).

Registered last on Purchase Order ``before_submit`` in ``hooks.py``, after the two submit
gates and the approval stamp. It only ever adds an orange message to the submit response.

**Why it matters.** Stock received against a Purchase Order line is valued at that line's
rate, so a $0 line brings its stock in at $0: the store under-reads, and every job the part
is later issued to is charged nothing for it. On 2026-09-24, 281 of the 327 submitted
Purchase Order lines dated in the previous 90 days had a rate of 0 (the Operations
dashboard's *Unpriced PO Lines* KPI counts them nightly).

**Why a warning.** Nik's decision of 2026-09-24 (TASK-2026-02238): warn, never block. With
most lines unpriced today, a refusal would stop purchasing outright on the day it shipped,
and an order whose price is genuinely not known yet still has to reach the supplier. The
message says what the $0 costs and where the rule is, and ``Update Items`` can still set
the rate on the submitted order before the goods arrive.

Silent during an import, a migrate, an install or a patch, and outside a web request: a
message queued with nobody to read it is noise in a job log. Never raises. A warning must
not be the reason an order cannot be submitted.
"""

import html

import frappe
from frappe import _

#: A rate below half a cent rounds to $0.00 on the order and on the receipt.
ZERO_RATE_BELOW = 0.005

SKIP_FLAGS = ("in_import", "in_migrate", "in_install", "in_patch")


def warn_zero_rate_lines(doc, method=None):
	"""Purchase Order → ``before_submit``. An orange message listing the $0 lines, or nothing."""
	try:
		if any(getattr(frappe.flags, name, None) for name in SKIP_FLAGS):
			return
		if not getattr(frappe.local, "request", None):
			return
		rows = zero_rate_rows(doc.get("items"))
		if not rows:
			return
		frappe.msgprint(warning_message(rows), title=_("Unpriced lines"), indicator="orange")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Purchase Order $0 line warning failed")


# --- pure helpers (no I/O; tests/test_po_price_check.py) ------------------------


def _number(value):
	"""A float from whatever a row carries — None, "", a string, a Decimal. Unreadable is 0.

	Unreadable reads as 0 on purpose: a rate nobody can read is a line nobody priced.
	"""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _get(row, key):
	if isinstance(row, dict):
		return row.get(key)
	getter = getattr(row, "get", None)
	if callable(getter):
		return getter(key)
	return getattr(row, key, None)


def zero_rate_rows(rows):
	"""The item rows whose ``rate`` is $0, as ``[{"idx", "item_code", "qty", "uom"}]``.

	``idx`` is the row number the order shows; a row without one (a hand-built dict) is
	numbered by position. A line discounted to nothing counts: ``rate`` is after discount,
	and $0 after discount is what the receipt will value the stock at.
	"""
	out = []
	for position, row in enumerate(rows or (), start=1):
		if abs(_number(_get(row, "rate"))) >= ZERO_RATE_BELOW:
			continue
		out.append(
			{
				"idx": _get(row, "idx") or position,
				"item_code": _get(row, "item_code") or "",
				"qty": _number(_get(row, "qty")),
				"uom": _get(row, "uom") or _get(row, "stock_uom") or "",
			}
		)
	return out


def _qty(value):
	return f"{value:g}"


def warning_message(rows):
	"""The HTML for the warning. Every value from the order is escaped."""
	items = "".join(
		"<li>"
		+ _("Row {0}: {1}, qty {2}").format(
			html.escape(str(row["idx"])),
			html.escape(str(row["item_code"])),
			html.escape(" ".join(part for part in (_qty(row["qty"]), str(row["uom"] or "")) if part)),
		)
		+ "</li>"
		for row in rows
	)
	lead = (
		_("This order has a line with no price:")
		if len(rows) == 1
		else _("This order has {0} lines with no price:").format(len(rows))
	)
	return (
		f"<p>{lead}</p><ul>{items}</ul><p>"
		+ _(
			"Stock received against these lines comes in at $0, so it is valued at nothing and any "
			"job it is issued to is charged nothing for it. POL-0602 section 4.6 requires a price on "
			"every line. Set the rate with <b>Update Items</b> before the goods are received."
		)
		+ "</p>"
	)
