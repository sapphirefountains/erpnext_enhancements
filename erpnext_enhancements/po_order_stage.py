"""Where a Purchase Order actually is, which ERPNext's own `status` cannot say.

Five stages, in `Purchase Order.custom_order_stage`:

    Created → Awaiting Confirmation → Waiting for Delivery │ Waiting for Pickup → Received

**Why this is a new field and not three more options on `status`.** It is the obvious
thing to try, and it does not work. ERPNext's `status` is *computed*, not stored-as-set:
`erpnext.controllers.status_updater.status_map["Purchase Order"]` recalculates it from
`docstatus`, `per_received`, `per_billed` and `advance_payment_status` on every save and
every receipt. Only three values survive a save at all — `Delivered`, `On Hold` and
`Closed` — and only because their rule is self-referential (`eval:self.status=='Closed'`).
An added option has no such rule, so it would be silently overwritten with whatever eval
matched next, almost always `To Receive and Bill`. The dropdown would look right in the
Desk, the save would succeed, nothing would log, and the value would be gone. Verified
against the live map on production rather than assumed.

So the two fields answer different questions and both are real: `status` is *ERPNext's*
account of the paperwork (received? billed? closed?), and `custom_order_stage` is *ours* —
have we actually placed this order, and are we driving to collect it or waiting on a
truck. That is also why the stage is `allow_on_submit`: every transition the buyer cares
about happens after submission.

**Manual, with one automatic exception.** Submitting a PO in ERPNext is approval, not the
act of sending the order to a supplier, so nothing advances the stage on submit — a stage
that lied about "Awaiting Confirmation" would be worse than one that is merely stale. The
exception is the far end: full receipt is a fact ERPNext already knows, so a Purchase
Receipt that completes the order sets `Received` on its own.

**The cancel path deliberately loses information rather than inventing it.** When a
Purchase Receipt is cancelled, `Received` is now false, but nothing on the document records
which of the two waiting stages preceded it. Inferring it from the shipping address was the
first idea and it is a coin flip wearing a lab coat: 147 of the 157 Purchase Orders on this
site carry a shipping address, so that rule answers "Waiting for Delivery" essentially
always. Falling back to `Awaiting Confirmation` instead says strictly less — "ordered, not
here yet", which is exactly what a cancelled receipt leaves true — and a timeline comment
names the receipt so a human can restore the specific stage in one click.

Purchase Receipts are barely used here (2 of 157 orders are fully received), so both
automatic paths are low-traffic. They are written to be correct rather than clever, and
both swallow-and-log: a stage update must never be the reason a receipt fails to submit.
"""

import frappe

FIELD = "custom_order_stage"

CREATED = "Created"
AWAITING_CONFIRMATION = "Awaiting Confirmation"
WAITING_FOR_DELIVERY = "Waiting for Delivery"
WAITING_FOR_PICKUP = "Waiting for Pickup"
RECEIVED = "Received"

# Order matters: this is the Select's option list, top to bottom, and the two waiting
# stages are siblings rather than a sequence -- an order is delivered or collected, never
# both. Renaming any of these needs a data patch; rows whose stored value is no longer a
# valid option refuse to save.
STAGES = (
	CREATED,
	AWAITING_CONFIRMATION,
	WAITING_FOR_DELIVERY,
	WAITING_FOR_PICKUP,
	RECEIVED,
)

DEFAULT_STAGE = CREATED

# A PO counts as fully received at this much of its quantity, matching the `>= 100` that
# erpnext's own status_map uses for "Completed". Kept as a name so the two automatic paths
# cannot drift apart on the boundary.
FULLY_RECEIVED = 100.0


def backfill_stage_for(docstatus, status, per_received):
	"""The stage an *existing* Purchase Order should have had, as a pure function.

	Lives here rather than in the patch so it can be tested bench-free, and so the rule
	is readable next to the stages it produces.

	Two of these mappings are inferences and are worth naming out loud:

	* **Closed and Completed both map to `Received`.** `Received` is the only terminal
	  stage, and 81 of the 157 historical orders are `Closed` having never been receipted
	  at all. Mapping them anywhere else leaves them sitting in the waiting-on list
	  forever, which is the one thing this field exists to prevent. It means "we are no
	  longer waiting on this", inferred from ERPNext having closed the order — NOT
	  evidence that goods were physically received.
	* **A submitted order that is none of the above becomes `Awaiting Confirmation`**, the
	  weakest true statement available: it has been committed, and it is not here yet.
	  Whether the buyer is expecting a truck or a trip is not recorded anywhere, so it is
	  not guessed.

	Drafts and cancelled orders keep the default. A cancelled order is not waiting on
	anyone, and calling it `Received` would be flatly untrue.
	"""
	if docstatus != 1:
		return DEFAULT_STAGE
	if float(per_received or 0) >= FULLY_RECEIVED:
		return RECEIVED
	if status in ("Closed", "Completed"):
		return RECEIVED
	return AWAITING_CONFIRMATION


def _stage_field_exists():
	"""The hooks fire during erpnext's own test bootstrap, before the patch that creates
	the field has run on a fresh database. Same guard as every other custom-field read in
	this app, and for the same reason: without it a fresh install crashes."""
	try:
		return bool(frappe.db.has_column("Purchase Order", FIELD))
	except Exception:
		return False


def _linked_purchase_orders(receipt):
	"""The distinct Purchase Orders a Purchase Receipt draws on."""
	names = set()
	for row in receipt.get("items") or []:
		name = row.get("purchase_order") if hasattr(row, "get") else getattr(row, "purchase_order", None)
		if name:
			names.add(name)
	return sorted(names)


def _per_received(po_name):
	"""Read fresh from the database, never from a cached document.

	erpnext updates `per_received` with a direct `db_set` from the receipt's own
	`on_submit`, so any Purchase Order document loaded earlier in this request still
	holds the pre-receipt value.
	"""
	return float(frappe.db.get_value("Purchase Order", po_name, "per_received") or 0)


def _set_stage(po_name, stage):
	"""`update_modified = False` on purpose: these orders are submitted and somebody may
	have the form open. Bumping `modified` from a different document's hook would hand
	them a TimestampMismatch on their next save for a field they did not touch."""
	frappe.db.set_value("Purchase Order", po_name, FIELD, stage, update_modified=False)


def advance_on_receipt(doc, method=None):
	"""`on_submit` on Purchase Receipt: mark every order it completes as Received.

	Registered as a doc_event, so it runs *after* the controller's own `on_submit` has
	pushed `per_received` back onto the orders — which is the only reason reading it here
	gives the post-receipt figure.

	Partial receipts are left alone: the order is still waiting on the rest of the goods,
	and the stage should keep saying so.
	"""
	try:
		if not _stage_field_exists():
			return
		advanced = []
		for po_name in _linked_purchase_orders(doc):
			if _per_received(po_name) < FULLY_RECEIVED:
				continue
			if frappe.db.get_value("Purchase Order", po_name, FIELD) == RECEIVED:
				continue
			_set_stage(po_name, RECEIVED)
			advanced.append(po_name)
		if advanced:
			frappe.logger().info(f"Order stage: {', '.join(advanced)} -> {RECEIVED} by {doc.name}")
	except Exception:
		# Swallowed on purpose. A stage that failed to advance is a wrong label; a stage
		# that raised would be a Purchase Receipt that cannot be submitted.
		frappe.log_error(frappe.get_traceback(), "Purchase Order stage advance")


def revert_on_receipt_cancel(doc, method=None):
	"""`on_cancel` on Purchase Receipt: take back a `Received` that is no longer true.

	Reverts to `Awaiting Confirmation` rather than to one of the two waiting stages. See
	the module docstring: which one it had been is recorded nowhere, and the shipping
	address cannot stand in for it on this site. The comment left on the order names the
	cancelled receipt so the specific stage can be restored deliberately.
	"""
	try:
		if not _stage_field_exists():
			return
		for po_name in _linked_purchase_orders(doc):
			if frappe.db.get_value("Purchase Order", po_name, FIELD) != RECEIVED:
				continue
			if _per_received(po_name) >= FULLY_RECEIVED:
				continue
			_set_stage(po_name, AWAITING_CONFIRMATION)
			frappe.get_doc("Purchase Order", po_name).add_comment(
				"Comment",
				(
					f"Order Stage moved from <b>{RECEIVED}</b> to <b>{AWAITING_CONFIRMATION}</b> "
					f"because Purchase Receipt {doc.name} was cancelled. Whether this order was "
					f"awaiting delivery or awaiting pickup is not recorded, so it was not guessed "
					f"— set it back if you know which."
				),
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Purchase Order stage revert")
