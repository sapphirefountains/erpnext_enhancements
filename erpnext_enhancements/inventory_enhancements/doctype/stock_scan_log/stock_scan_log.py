# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Stock Scan Log doctype.

One row per save on the Stock Scan page (``www/stock-scan.html``, endpoints in
``api/stock_scan.py``): who moved what, to or from which location, and the submitted
voucher that did it — a Stock Entry, or a Purchase Receipt for stock received against an
order. The voucher is the stock ledger's record; this row is the page's, and it carries
three things the voucher cannot:

* **Undo.** The page lists the caller's recent rows and cancels the voucher behind one.
* **The review queue.** Stock added without a purchase order sets ``needs_review``; a
  Stock Manager ticks ``reviewed`` once they know where it came from. A store-run line
  (v1.536.0) is reviewed by Purchasing, and never by the person who recorded it.
* **Idempotency.** ``client_ref`` is unique, so a save retried after a dropped connection
  cannot post twice (see ``api.stock_scan``).

Rows are written by the endpoints only (the doctype is ``in_create``). After insert, the
only fields a person may change are the review fields; the rest is history.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime

#: Fields a person may edit on a saved row. Everything else is set by the endpoint that
#: wrote the row, or by undo, and changing it by hand would make the log disagree with
#: the voucher it points at. ``reviewed_by`` and ``reviewed_on`` are deliberately NOT here:
#: they are stamped by :meth:`StockScanLog._stamp_review` after this check runs, so a client
#: can tick Reviewed but cannot say who reviewed it or when. ``read_only`` on the field is a
#: form hint only -- v16's REST update applies whatever the body carries.
EDITABLE_AFTER_INSERT = frozenset({"reviewed", "review_note"})

#: Fields undo writes. ``api.stock_scan.undo`` sets ``flags.stock_scan_undo`` to pass them.
UNDO_FIELDS = frozenset({"status", "undone_by", "undone_at"})

#: ``stock_scan_rules.ACTION_STORE_RUN``, spelled here so the controller imports nothing of the page's.
STORE_RUN = "Store Run"


class StockScanLog(Document):
	def validate(self):
		self._keep_history_immutable()
		self._stamp_review()

	def _keep_history_immutable(self):
		if self.is_new():
			return
		allowed = set(EDITABLE_AFTER_INSERT)
		if self.flags.get("stock_scan_undo"):
			allowed |= UNDO_FIELDS
		before = self.get_doc_before_save()
		if not before:
			return
		for df in self.meta.fields:
			fieldname = df.fieldname
			if df.fieldtype in ("Section Break", "Column Break", "Tab Break") or fieldname in allowed:
				continue
			if _comparable(df.fieldtype, before.get(fieldname)) != _comparable(
				df.fieldtype, self.get(fieldname)
			):
				frappe.throw(
					_(
						"{0} cannot be changed on a Stock Scan Log. Undo the scan and scan again instead."
					).format(_(df.label or fieldname))
				)

	def _stamp_review(self):
		"""Record who ticked Reviewed and when, at the moment they tick it.

		Runs after :meth:`_keep_history_immutable`, which has already refused any attempt to
		set these two fields by hand. Stamped on the transition to reviewed rather than
		whenever they are empty, so re-saving a reviewed row keeps the original reviewer.
		"""
		if self.reviewed and not self.needs_review:
			self.reviewed = 0
		if not self.reviewed:
			self.reviewed_by = None
			self.reviewed_on = None
			return
		before = None if self.is_new() else self.get_doc_before_save()
		if not (before and before.reviewed):
			# A store run is a purchase, and the review is Purchasing checking it (POL-0602 §4.8).
			# Stock Manager -- every technician -- has write on this log, so without this a
			# technician could tick Reviewed on their own run, which also ends their Undo.
			if self.action == STORE_RUN and self.posted_by == frappe.session.user:
				frappe.throw(_("You recorded this store run, so someone else reviews it (POL-0602 §4.8)."))
			self.reviewed_by = frappe.session.user
			self.reviewed_on = now_datetime()


def _comparable(fieldtype, value):
	"""A field value in a form that compares equal across the Desk's string round trip.

	A form save sends datetimes back as strings and numbers as JSON numbers, while the row
	loaded for comparison holds ``datetime`` and ``float``. Compared raw, an untouched
	``posted_at`` would read as changed and every review save would be refused.
	"""
	if value in (None, ""):
		return None
	if fieldtype in ("Float", "Currency", "Percent"):
		return flt(value)
	if fieldtype in ("Int", "Check"):
		return cint(value)
	if fieldtype == "Datetime":
		return get_datetime(value)
	return cstr(value)
