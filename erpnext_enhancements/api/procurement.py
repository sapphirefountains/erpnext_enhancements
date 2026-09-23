"""Supplier purchase-link endpoints for procurement screens.

Whitelisted API consumed by ``public/js/procurement_links.js`` to show and edit
the "purchase URL" stored on Item Supplier child rows (e.g. quick links to a
vendor's product page from a Purchase Order).

Security: ``save_item_link`` writes the Item with ``ignore_permissions=True`` so a
buyer can record a supplier URL without needing full Item write access — but that
elevation is gated to purchasing roles (``_require_purchasing_access``). Without a
gate, any authenticated user could write an arbitrary ``purchase_url`` onto any Item,
and buyers later click that link straight from the Purchase Order screen. No external
services.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowtime

#: Roles that may read/write supplier purchase links. Purchasing staff plus the
#: administrative roles that manage Items. A user outside this set has no business
#: writing a URL that other buyers will click.
ALLOWED_ROLES = frozenset(
    {"Purchase User", "Purchase Manager", "Purchase Master Manager", "Item Manager", "System Manager"}
)


def _require_purchasing_access():
    if ALLOWED_ROLES.isdisjoint(frappe.get_roles()):
        frappe.throw(
            _("You are not permitted to manage supplier purchase links."),
            frappe.PermissionError,
        )


@frappe.whitelist()
def get_item_links(item_codes, supplier=None):
    """
    Fetches purchase URLs for a list of items.
    If 'supplier' is provided (e.g. on a PO), filters to that supplier.
    """
    _require_purchasing_access()

    if isinstance(item_codes, str):
        item_codes = frappe.parse_json(item_codes)

    if not item_codes:
        return {}

    filters = {"parent": ["in", item_codes]}

    # Strict filtering: If PO has a supplier, only return that supplier's link
    if supplier:
        filters["supplier"] = supplier

    links = frappe.get_all("Item Supplier",
        filters=filters,
        fields=["parent", "supplier", "purchase_url"]
    )

    # Group by Item Code
    grouped_links = {}
    for link in links:
        if not link.purchase_url:
            continue

        if link.parent not in grouped_links:
            grouped_links[link.parent] = []

        grouped_links[link.parent].append({
            "supplier": link.supplier,
            "url": link.purchase_url
        })

    return grouped_links

@frappe.whitelist()
def save_item_link(item_code, supplier, url):
    """
    Updates or creates an Item Supplier row with the given URL.
    """
    _require_purchasing_access()

    if not url:
        return

    # Check if this supplier already exists for the item
    exists = frappe.db.exists("Item Supplier", {"parent": item_code, "supplier": supplier})

    if exists:
        frappe.db.set_value("Item Supplier", exists, "purchase_url", url)
    else:
        # Create new row
        item_doc = frappe.get_doc("Item", item_code)
        item_doc.append("supplier_items", {
            "supplier": supplier,
            "purchase_url": url
        })
        item_doc.save(ignore_permissions=True) # Allow User to save even if they don't have Item write access

    return True


def cascade_expected_delivery_date(doc, method=None):
    """``before_validate`` hook for Purchase Order: fill blank item delivery dates.

    ER-2026-362239 asked for a delivery expectation on the order *and* per item, "in
    case one item in the order gets delayed more than the others". So the header value
    is a starting point, not a truth: it fills rows that have said nothing, and never
    touches a row that carries its own date.

    **This is not the Required By cascade, and must not become it.** ERPNext already
    cascades ``schedule_date`` in ``buying_controller.validate_schedule_date()``, and
    does more than cascade it — it pulls the header *up* to the earliest row
    (``self.schedule_date = min(...)``) and throws on any row that predates
    ``transaction_date``. Required By is when we need the goods; Expected Delivery is
    when the supplier says they will arrive. Two facts, two fields, and the second had
    nowhere to live until now.

    **One direction only.** No roll-up from the rows: unlike Required By, where
    earliest-wins is a real constraint on the order, an order-level delivery expectation
    is the buyer's own statement, and inferring it from the rows would silently rewrite
    what they typed.

    Runs as ``before_validate`` so it lands ahead of Frappe's mandatory check, matching
    ``procurement_project.cascade_project_to_items`` alongside it.
    """
    header_date = doc.get("custom_expected_delivery_date")
    if not header_date:
        return

    for row in doc.get("items") or []:
        if not row.get("expected_delivery_date"):
            row.expected_delivery_date = header_date


# ---------------------------------------------------------------------------
# Receive Items on the Purchase Order (ER-2026-458194, TASK-2026-02045)
# ---------------------------------------------------------------------------


def _typed_quantities(rows):
    """``rows`` as the dialog posts them -> ``{row name: qty}``.

    Junk is dropped rather than raised on: the planner reports a row it cannot place, and a
    quantity that will not parse is zero, which is "nothing arrived on that line".
    """
    if isinstance(rows, str):
        rows = frappe.parse_json(rows)
    typed = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        name = (raw.get("purchase_order_item") or "").strip()
        if name:
            typed[name] = flt(raw.get("qty"))
    return typed


def _over_receipt_allowance(item_code):
    """The over-receipt allowance ERPNext applies to this item, in percent.

    Same precedence as ``erpnext.controllers.status_updater.get_allowance_for``: the Item's
    own ``over_delivery_receipt_allowance`` when set, else Stock Settings'. On production
    both are 0, so a receipt above pending is refused; the rule is read rather than assumed
    so the dialog's refusal and ERPNext's own check on submit can never disagree.
    """
    item_level = 0.0
    if item_code and frappe.get_meta("Item").has_field("over_delivery_receipt_allowance"):
        item_level = flt(frappe.get_cached_value("Item", item_code, "over_delivery_receipt_allowance"))
    if item_level:
        return item_level
    return flt(frappe.db.get_single_value("Stock Settings", "over_delivery_receipt_allowance"))


def _receiving_warehouse(warehouse, company):
    """The warehouse a receipt is put into, refused in the receiver's terms before a document
    exists.

    Only the Stock Scan page passes one: it receives into the location whose QR label was just
    scanned rather than the order line's own warehouse. ERPNext refuses the same things later
    -- ``StockController.validate_warehouse`` (company, disabled) and the Stock Ledger Entry's
    ``block_transactions_against_group_warehouse`` (group) -- so this adds no rule, only the
    sentence.
    """
    row = frappe.db.get_value(
        "Warehouse", warehouse, ["name", "company", "is_group", "disabled"], as_dict=True
    )
    if not row:
        frappe.throw(_("Location {0} was not found.").format(warehouse))
    if row.is_group:
        frappe.throw(
            _("{0} is a group of locations. Receive into one of the locations inside it.").format(row.name)
        )
    if row.disabled:
        frappe.throw(_("{0} is disabled.").format(row.name))
    if row.company != company:
        frappe.throw(_("{0} belongs to {1}, not {2}.").format(row.name, row.company, company))
    return row.name


@frappe.whitelist(methods=["POST"])
def receive_items(purchase_order, rows, posting_date=None, warehouse=None, remarks=None):
    """Write and submit a Purchase Receipt for what just arrived against one order.

    The Receive Items dialog on a submitted Purchase Order (``public/js/po_receive_items.js``,
    ER-2026-458194 / TASK-2026-02045) posts here. ``rows`` is a list of
    ``{"purchase_order_item": <row name>, "qty": <arrived now>}``; rows left at zero are
    not sent, or are dropped.

    Why a real receipt and not a number on the order: submitting the receipt is what moves
    ``Purchase Order Item.received_qty``, ``per_received`` and the status pill through
    ERPNext's ``status_updater``, and ``po_order_stage.advance_on_receipt`` then sets the
    Order Stage to Partially Fulfilled or Received on its own. A quantity typed straight
    onto the order would diverge from the receipts the packing-slip intake and Create >
    Purchase Receipt already produce, and from every report that reads ``received_qty``.

    Things this is careful about:

    * **It is ERPNext's own mapping.** ``make_purchase_receipt`` with ``filtered_children``
      builds the receipt exactly as Create > Purchase Receipt does -- only lines with
      something pending, quantity pre-filled with the pending amount, taxes reset, drop-ship
      lines excluded. The one thing done afterwards is to set each line's accepted quantity
      to what the buyer typed. ``stock_qty``, the received quantity in stock UOM and every
      amount are recomputed by the receipt's own ``validate``
      (``buying_controller.set_qty_as_per_stock_uom`` and ``calculate_taxes_and_totals``),
      so nothing here can drift from the controller's arithmetic.
    * **Over-receipt is refused before a document exists**, in the buyer's terms, using the
      allowance ERPNext would apply (the Item's own ``over_delivery_receipt_allowance``, else
      Stock Settings'). ERPNext re-checks on submit; that check is the backstop, this one is
      the message. The rule itself is ``procurement_quantities.plan_receipt``, pure and
      tested bench-free.
    * **Permissions are the framework's.** ``insert`` and ``submit`` run without
      ``ignore_permissions``, so a user who could not create and submit a Purchase Receipt by
      hand cannot do it from here either. The order itself needs read.
    * **The posting date is optional and defaults to today.** A date is honoured through
      ``set_posting_time``; without that flag ``validate_posting_time`` resets the date to
      now, silently.
    * **A draft receipt already open against the order is not netted off.** Neither is it by
      Create > Purchase Receipt: ``received_qty`` moves only on submit. Submitting that draft
      later hits ERPNext's over-limit check, so nothing double-counts in the ledger, but the
      draft will need editing. The packing-slip intake creates such drafts.
    * **``warehouse`` puts the goods somewhere other than the order line's warehouse.** The
      Stock Scan page (v1.521.0, through :func:`receive_order_line`) receives into the bin
      whose QR label was scanned. It grants nothing new: a user who may create and submit a
      receipt can already change the warehouse on Create > Purchase Receipt. Both the header
      ``set_warehouse`` and every row are set, because ERPNext never pushes the header into
      the rows on the server and only *clears* a header that disagrees with them. The order's
      ``Bin.ordered_qty`` still falls at the line's own warehouse and ``actual_qty`` rises at
      the scanned one -- "on order" drops where it was ordered for, "on hand" rises where it
      was put, which is right.
    * **``remarks``** replaces the receipt's remarks, so the page can say on the voucher that
      it came from a scan and who did it.
    """
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    from erpnext_enhancements.procurement_quantities import plan_receipt

    po = frappe.get_doc("Purchase Order", purchase_order)
    po.check_permission("read")
    if po.docstatus != 1:
        frappe.throw(_("Only a submitted Purchase Order can be received against."))
    if po.status in ("Closed", "On Hold"):
        frappe.throw(_("{0} is {1}. Reopen it before receiving against it.").format(po.name, _(po.status)))
    for ptype in ("create", "submit"):
        if not frappe.has_permission("Purchase Receipt", ptype):
            frappe.throw(
                _("You need permission to {0} a Purchase Receipt.").format(ptype), frappe.PermissionError
            )
    target = _receiving_warehouse(warehouse, po.company) if warehouse else None

    order_lines = [
        {
            "name": row.name,
            "item_code": row.item_code,
            "qty": row.qty,
            "received_qty": row.received_qty,
            "delivered_by_supplier": row.delivered_by_supplier,
            "allowance_pct": _over_receipt_allowance(row.item_code),
        }
        for row in po.items
    ]
    lines, problems = plan_receipt(order_lines, _typed_quantities(rows))
    if problems:
        frappe.throw(
            "<br>".join(frappe.utils.escape_html(problem) for problem in problems),
            title=_("Nothing was received"),
        )
    if not lines:
        frappe.throw(_("Nothing to receive: every line is at 0."))

    wanted = {line["purchase_order_item"]: line["qty"] for line in lines}
    receipt = make_purchase_receipt(po.name, args={"filtered_children": list(wanted)})
    if target:
        receipt.set_warehouse = target
    for item in receipt.items:
        # Accepted quantity is what was typed; nothing rejected. The receipt's own validate
        # recomputes stock_qty, received_stock_qty and every amount from these.
        item.qty = wanted[item.purchase_order_item]
        item.received_qty = item.qty
        item.rejected_qty = 0
        if target:
            item.warehouse = target
    if remarks:
        receipt.remarks = remarks
    if posting_date:
        receipt.set_posting_time = 1
        receipt.posting_date = getdate(posting_date)
        receipt.posting_time = nowtime()
    receipt.insert()
    receipt.submit()

    fields = ["per_received", "status"]
    if frappe.db.has_column("Purchase Order", "custom_order_stage"):
        fields.append("custom_order_stage")
    after = frappe.db.get_value("Purchase Order", po.name, fields, as_dict=True) or {}
    return {
        "purchase_receipt": receipt.name,
        "received": [
            {"purchase_order_item": item.purchase_order_item, "item_code": item.item_code, "qty": item.qty}
            for item in receipt.items
        ],
        "per_received": after.get("per_received"),
        "status": after.get("status"),
        "order_stage": after.get("custom_order_stage"),
    }


def receive_order_line(purchase_order_item, stock_qty, warehouse, item_code=None, remarks=None):
    """Receive ONE Purchase Order line, counted in the item's STOCK UOM, into ``warehouse``.

    The Stock Scan page's "+" when the scanned item has an open order line
    (``api.stock_scan.add``). Deliberately owns no check of its own beyond the unit
    conversion: every refusal -- read on the order, submitted, not Closed or On Hold,
    Purchase Receipt create + submit, over-receipt through ``plan_receipt`` -- is
    :func:`receive_items`', so the scan page and the order's Receive Items dialog cannot
    disagree about what may be received. Not whitelisted: the page reaches it only through
    its own gated endpoint.

    The page counts in the stock UOM; the order line may be in another (a Box of 10), and a
    receipt row's UOM must equal the order line's. ``stock_scan_rules.to_order_uom`` converts
    and refuses a result that is not whole in a whole-number UOM rather than rounding it --
    rounding would receive goods that did not arrive.
    """
    from erpnext_enhancements.inventory_enhancements.stock_scan_rules import to_order_uom

    line = frappe.db.get_value(
        "Purchase Order Item",
        purchase_order_item,
        ["parent", "parenttype", "item_code", "uom", "conversion_factor"],
        as_dict=True,
    )
    if not line or line.parenttype != "Purchase Order":
        frappe.throw(_("Purchase Order line {0} was not found.").format(purchase_order_item))
    if item_code and line.item_code != item_code:
        frappe.throw(_("That order line is for {0}, not {1}.").format(line.item_code, item_code))
    whole = cint(frappe.get_cached_value("UOM", line.uom, "must_be_whole_number")) if line.uom else 0
    qty, problem = to_order_uom(flt(stock_qty), line.conversion_factor, whole_number=bool(whole), uom=line.uom)
    if problem:
        frappe.throw(problem, title=_("Nothing was received"))
    return receive_items(
        line.parent,
        [{"purchase_order_item": purchase_order_item, "qty": qty}],
        warehouse=warehouse,
        remarks=remarks,
    )
