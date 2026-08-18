"""Where a Purchase Order actually is, which ERPNext's own `status` cannot say.

Seven stages, in `Purchase Order.custom_order_stage`:

    Created → Awaiting Confirmation → Awaiting Fulfillment
            → Waiting for Delivery │ Waiting for Pickup
            → Partially Fulfilled → Received

The second row is a fork, not a step: an order is delivered or it is collected, never both.
`Awaiting Fulfillment` is the stage before that fork — the supplier has answered and is
working on it, and how the goods will arrive is not yet the question.

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

**Manual, with two automatic exceptions, both at the far end.** Submitting a PO in ERPNext
is approval, not the act of sending the order to a supplier, so nothing advances the stage
on submit — a stage that lied about "Awaiting Confirmation" would be worse than one that is
merely stale. What *is* automatic is receipt, because it is a fact ERPNext already knows: a
Purchase Receipt that completes the order sets `Received`, and one that covers part of it
sets `Partially Fulfilled`.

The partial case was deliberately a no-op until v1.328.0, on the grounds that the order was
still waiting on the rest of the goods and the stage should keep saying so. That reasoning
held only while no option could say "some of it is here" — `Partially Fulfilled` now can,
and it is a stronger statement than the stage it replaces, not a weaker one. It does cost
something: moving off `Waiting for Pickup` loses the fact that the remainder is a trip
rather than a truck, so that transition leaves a timeline comment naming the stage it
replaced, exactly as the cancel path below does. Moving off `Created` or `Awaiting
Confirmation` loses nothing and says nothing.

**The cancel path deliberately loses information rather than inventing it.** When a
Purchase Receipt is cancelled the stage is recomputed from `per_received` rather than
remembered — still complete stays `Received`, part-received becomes `Partially Fulfilled`,
and nothing received falls back to `Awaiting Confirmation`. Only that last case loses
anything, and what it loses is which of the two waiting stages preceded it, which is
recorded nowhere. Inferring it from the shipping address was the first idea and it is a
coin flip wearing a lab coat: 147 of the 157 Purchase Orders on this site carry a shipping
address, so that rule answers "Waiting for Delivery" essentially always. Falling back to `Awaiting Confirmation` instead says strictly less — "ordered, not
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
AWAITING_FULFILLMENT = "Awaiting Fulfillment"
WAITING_FOR_DELIVERY = "Waiting for Delivery"
WAITING_FOR_PICKUP = "Waiting for Pickup"
PARTIALLY_FULFILLED = "Partially Fulfilled"
RECEIVED = "Received"

# Order matters: this is the Select's option list, top to bottom, and the two waiting
# stages are siblings rather than a sequence -- an order is delivered or collected, never
# both. Renaming any of these needs a data patch; rows whose stored value is no longer a
# valid option refuse to save. ADDING one, as v1.328.0 did twice, needs no data migration
# at all -- every value already stored is still in the list. The two directions are not
# symmetrical and it is worth knowing which one you are doing.
STAGES = (
	CREATED,
	AWAITING_CONFIRMATION,
	AWAITING_FULFILLMENT,
	WAITING_FOR_DELIVERY,
	WAITING_FOR_PICKUP,
	PARTIALLY_FULFILLED,
	RECEIVED,
)

# Stages that record something `Partially Fulfilled` cannot: whether the supplier has
# confirmed, and whether the rest of the order is a delivery or a trip. Moving off one of
# these into `Partially Fulfilled` loses that, so it earns a timeline comment. Moving off
# `Created` or `Awaiting Confirmation` loses nothing, and a comment on every partial
# receipt would be noise on the documents that need it least.
INFORMATIVE_STAGES = (
	AWAITING_FULFILLMENT,
	WAITING_FOR_DELIVERY,
	WAITING_FOR_PICKUP,
)

DEFAULT_STAGE = CREATED

# A PO counts as fully received at this much of its quantity, matching the `>= 100` that
# erpnext's own status_map uses for "Completed". Kept as a name so the two automatic paths
# cannot drift apart on the boundary.
FULLY_RECEIVED = 100.0


def field_definition():
	"""The Custom Field spec for `custom_order_stage`, in one place.

	Two patches create and then widen this field, and a hard-coded option list in either of
	them would drift from the `STAGES` the hooks and reports use — a stored value outside the
	Select makes the row refuse to save, and it would not be noticed until somebody tried to
	edit an order months later. Both patches call this instead.

	Three of the properties are load-bearing rather than decorative:

	* **`allow_on_submit`.** Every transition the buyer cares about — ordered, confirmed,
	  collected — happens after the order is submitted. Without this the field would be
	  editable only in the one state where it is always `Created`, i.e. useless.
	* **`insert_after: tracking_section`** puts it at the top of the Order Status section,
	  above the `status` ERPNext computes. Deliberately not `insert_after: status`, which is
	  already the anchor for `custom_approval_stamp_section`; two custom fields sharing an
	  anchor resolve in an order nobody controls, and losing that race would drop this field
	  inside the collapsible Approval section.
	* **`in_standard_filter`** is the actual feature for a list of a hundred-odd orders:
	  "what am I waiting on" is a filter, not a scroll.
	"""
	return {
		"fieldname": FIELD,
		"label": "Order Stage",
		"fieldtype": "Select",
		# No leading blank option: the default fills every row, so the field always holds
		# one of the stages and reports can rely on it.
		"options": "\n".join(STAGES),
		"default": DEFAULT_STAGE,
		"insert_after": "tracking_section",
		"allow_on_submit": 1,
		"no_copy": 1,
		"in_standard_filter": 1,
		"in_list_view": 1,
		"description": (
			"Where this order is with the supplier. Set by hand — submitting an order "
			"here is approval, not the act of placing it. Only the last two arrive on "
			"their own: a Purchase Receipt that covers part of the order sets "
			"'Partially Fulfilled', and one that completes it sets 'Received'."
		),
	}


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
	* **A part-received order becomes `Partially Fulfilled`.** Before v1.328.0 there was no
	  such option and these fell through to `Awaiting Confirmation`, which was false the
	  moment the first box arrived. Nothing was re-backfilled when the option was added
	  because nothing needed to be: production had **zero** part-received orders at the time
	  (158 orders, checked), so the old rule and the new one disagree on no stored row.
	* **A submitted order that is none of the above becomes `Awaiting Confirmation`**, the
	  weakest true statement available: it has been committed, and it is not here yet.
	  Whether the buyer is expecting a truck or a trip is not recorded anywhere, so it is
	  not guessed.

	Drafts and cancelled orders keep the default. A cancelled order is not waiting on
	anyone, and calling it `Received` would be flatly untrue.
	"""
	if docstatus != 1:
		return DEFAULT_STAGE
	received = float(per_received or 0)
	if received >= FULLY_RECEIVED:
		return RECEIVED
	if status in ("Closed", "Completed"):
		return RECEIVED
	if received > 0:
		return PARTIALLY_FULFILLED
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


def _note_stage_change(po_name, was, now, because):
	"""Leave the losing information on the timeline rather than throwing it away.

	Both automatic paths overwrite a stage a human set, and in both the stage they write is
	true but says *less* than the one it replaced -- `Partially Fulfilled` does not record
	whether the remainder is a delivery or a trip, and neither does `Awaiting Confirmation`.
	A comment naming the old stage costs one row and turns an unrecoverable overwrite into a
	one-click restore.
	"""
	frappe.get_doc("Purchase Order", po_name).add_comment(
		"Comment",
		(
			f"Order Stage moved from <b>{was}</b> to <b>{now}</b> because {because}. "
			f"Whether this order was awaiting delivery or awaiting pickup is not recorded, "
			f"so it was not guessed — set it back if you know which."
		),
	)


def advance_on_receipt(doc, method=None):
	"""`on_submit` on Purchase Receipt: move every order it touches to where it now is.

	Registered as a doc_event, so it runs *after* the controller's own `on_submit` has
	pushed `per_received` back onto the orders -- which is the only reason reading it here
	gives the post-receipt figure.

	Full receipt lands on `Received`, part receipt on `Partially Fulfilled`. A receipt that
	leaves an order at nothing received changes nothing: that is not a fact about this order.
	"""
	try:
		if not _stage_field_exists():
			return
		advanced = []
		for po_name in _linked_purchase_orders(doc):
			received = _per_received(po_name)
			if received >= FULLY_RECEIVED:
				target = RECEIVED
			elif received > 0:
				target = PARTIALLY_FULFILLED
			else:
				continue
			current = frappe.db.get_value("Purchase Order", po_name, FIELD)
			if current == target:
				continue
			# Never walk an order backwards out of `Received`. A second, smaller receipt
			# against an already-complete order does not make it less complete, and
			# `per_received` can read under 100 mid-flight on an amended document.
			if current == RECEIVED:
				continue
			_set_stage(po_name, target)
			advanced.append(po_name)
			if target == PARTIALLY_FULFILLED and current in INFORMATIVE_STAGES:
				_note_stage_change(po_name, current, target, f"Purchase Receipt {doc.name} was submitted")
		if advanced:
			frappe.logger().info(f"Order stage: {', '.join(advanced)} advanced by {doc.name}")
	except Exception:
		# Swallowed on purpose. A stage that failed to advance is a wrong label; a stage
		# that raised would be a Purchase Receipt that cannot be submitted.
		frappe.log_error(frappe.get_traceback(), "Purchase Order stage advance")


def revert_on_receipt_cancel(doc, method=None):
	"""`on_cancel` on Purchase Receipt: take back a stage that is no longer true.

	Only the two stages this module writes itself are eligible -- a hand-set
	`Waiting for Pickup` is somebody's own account of the order and a cancelled receipt is
	no reason to overrule it.

	Where it lands is read back off `per_received` rather than remembered: still complete
	stays `Received` (one of several receipts cancelled, the rest still cover the order),
	part-received becomes `Partially Fulfilled`, and nothing received falls back to
	`Awaiting Confirmation`. That last one is deliberately the weakest true statement and
	not one of the two waiting stages: which of them it had been is recorded nowhere, and the
	shipping address cannot stand in for it -- 147 of the 157 orders on this site carry one,
	so that rule answers "Waiting for Delivery" essentially always. See `_note_stage_change`.
	"""
	try:
		if not _stage_field_exists():
			return
		for po_name in _linked_purchase_orders(doc):
			current = frappe.db.get_value("Purchase Order", po_name, FIELD)
			if current not in (RECEIVED, PARTIALLY_FULFILLED):
				continue
			received = _per_received(po_name)
			if received >= FULLY_RECEIVED:
				target = RECEIVED
			elif received > 0:
				target = PARTIALLY_FULFILLED
			else:
				target = AWAITING_CONFIRMATION
			if current == target:
				continue
			_set_stage(po_name, target)
			_note_stage_change(po_name, current, target, f"Purchase Receipt {doc.name} was canceled")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Purchase Order stage revert")
