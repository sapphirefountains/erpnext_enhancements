"""Project Enhancements module.

Top-level project helper functions (formerly erpnext_enhancements/project_enhancements.py,
folded into this package __init__ during the multi-app consolidation). These are referenced
as erpnext_enhancements.project_enhancements.<fn> from hooks.py and client scripts."""

import frappe
from frappe import _

from erpnext_enhancements import procurement_quantities


@frappe.whitelist()
def get_procurement_status(project_name):
	"""
	Fetches the procurement status for items linked to a Project.
	Aggregates data from:
	1. Material Requests (External Purchasing & Internal Transfers)
	2. Direct Purchase Orders (No Material Request)
	"""
	if not project_name:
		return {}

	# Combined query for Material Request based flows and Direct Purchase Orders
	sql = """
        SELECT
            *
        FROM (
            /* PART 1: Material Request Flow (External & Internal) */
            SELECT
                'Material Request' as source_doctype,
                mr_item.item_code,
                mr_item.item_name,
                mr_item.qty as mr_qty,
                /* Per-line truth. These are ERPNext's own denormalized rollups on the
                   request line, so they are immune to the OR-join fan-out below (one
                   request line split across two Purchase Orders arrives as two rows)
                   and they already net out amendments, cancellations and returns.
                   Stock-UOM basis throughout: status_updater maintains ordered_qty and
                   received_qty against stock_qty, so stock_qty is the only denominator
                   that makes the ratio mean anything. */
                mr_item.name as mr_item_name,
                mr_item.stock_qty as mr_requested_qty,
                mr_item.ordered_qty as mr_ordered_qty,
                mr_item.received_qty as mr_received_qty,
                /* Both UOMs, so the displayed figure can say which one it is in. A
                   line requested in FT against a stock UOM of Unit is real on this
                   data, and the quantities above are the stock-UOM ones. */
                mr_item.uom as line_uom,
                mr_item.stock_uom as line_stock_uom,
                mr.name as mr_name,
                mr.transaction_date as transaction_date,
                mr.status as mr_status,
                rfq.name as rfq_name,
                rfq.status as rfq_status,
                sq.name as sq_name,
                sq.status as sq_status,
                po.name as po_name,
                po.status as po_status,
                po_item.name as po_item_name,
                po.docstatus as po_docstatus,
                po_item.qty as po_line_qty,
                po_item.received_qty as po_line_received_qty,
                pr.name as pr_name,
                pr.status as pr_status,
                pi.name as pi_name,
                pi.status as pi_status,
                se.name as se_name,
                CASE
                    WHEN se.docstatus=1 THEN 'Submitted'
                    WHEN se.docstatus=2 THEN 'Cancelled'
                    ELSE 'Draft'
                END as se_status,
                COALESCE(pr_item.warehouse, sed.t_warehouse) as warehouse,
                /* The old `po_item.qty as ordered_qty` and
                   `COALESCE(pr_item.qty, sed.qty) as received_qty` are gone: both were
                   *one chain row's* quantities being read as the line's totals, so an
                   MR line split across two Purchase Orders reported whichever row came
                   back, and a line received over two Purchase Receipts reported one of
                   them. The per-line rollups selected at the top of this SELECT answer
                   the same question correctly, Stock Entry receipts on internal
                   transfers included. */
                pr.is_subcontracted as is_subcontracted
            FROM
                `tabMaterial Request Item` mr_item
            JOIN
                `tabMaterial Request` mr ON mr.name = mr_item.parent
            LEFT JOIN
                `tabRequest for Quotation Item` rfq_item ON rfq_item.material_request_item = mr_item.name
            LEFT JOIN
                `tabRequest for Quotation` rfq ON rfq.name = rfq_item.parent
            LEFT JOIN
                `tabSupplier Quotation Item` sq_item ON sq_item.request_for_quotation_item = rfq_item.name
            LEFT JOIN
                `tabSupplier Quotation` sq ON sq.name = sq_item.parent
            LEFT JOIN
                `tabPurchase Order Item` po_item ON (
                    po_item.supplier_quotation_item = sq_item.name
                    OR po_item.material_request_item = mr_item.name
                )
            LEFT JOIN
                /* Cancelled orders are not part of the chain. Part 2 has always
                   filtered them; Part 1 never did, which was latent only because this
                   site has no cancelled Purchase Orders yet. */
                `tabPurchase Order` po ON po.name = po_item.parent AND po.docstatus < 2
            LEFT JOIN
                `tabPurchase Receipt Item` pr_item ON pr_item.purchase_order_item = po_item.name
            LEFT JOIN
                `tabPurchase Receipt` pr ON pr.name = pr_item.parent
            LEFT JOIN
                `tabPurchase Invoice Item` pi_item ON (
                    pi_item.pr_detail = pr_item.name
                    OR pi_item.po_detail = po_item.name
                )
            LEFT JOIN
                `tabPurchase Invoice` pi ON pi.name = pi_item.parent
            LEFT JOIN
                `tabStock Entry Detail` sed ON sed.material_request_item = mr_item.name
            LEFT JOIN
                `tabStock Entry` se ON se.name = sed.parent
            WHERE
                (
                    mr_item.project = %(project)s
                    OR mr.custom_project = %(project)s
                    OR rfq.custom_project = %(project)s
                )
                AND mr.docstatus < 2

            UNION ALL

            /* PART 2: Direct Purchase Orders */
            SELECT
                'Purchase Order' as source_doctype,
                po_item.item_code,
                po_item.item_name,
                0 as mr_qty,
                /* No Material Request behind this line. NULL, not 0 — "nobody asked"
                   and "asked for none" are different facts and the feed used to print
                   both as 0. */
                NULL as mr_item_name,
                NULL as mr_requested_qty,
                NULL as mr_ordered_qty,
                NULL as mr_received_qty,
                po_item.uom as line_uom,
                po_item.stock_uom as line_stock_uom,
                NULL as mr_name,
                po.transaction_date as transaction_date,
                NULL as mr_status,
                NULL as rfq_name,
                NULL as rfq_status,
                NULL as sq_name,
                NULL as sq_status,
                po.name as po_name,
                po.status as po_status,
                po_item.name as po_item_name,
                po.docstatus as po_docstatus,
                /* Transaction-UOM basis on this axis, deliberately unlike the request
                   line above: status_updater maintains Purchase Order Item.received_qty
                   against `qty`, not `stock_qty`, so comparing it to stock_qty would be
                   wrong the day a line carries a conversion factor. Every line on this
                   site has conversion_factor = 1 today, which is exactly why the error
                   would be invisible. */
                po_item.qty as po_line_qty,
                po_item.received_qty as po_line_received_qty,
                pr.name as pr_name,
                pr.status as pr_status,
                pi.name as pi_name,
                pi.status as pi_status,
                NULL as se_name,
                NULL as se_status,
                pr_item.warehouse as warehouse,
                pr.is_subcontracted as is_subcontracted
            FROM
                `tabPurchase Order Item` po_item
            JOIN
                `tabPurchase Order` po ON po.name = po_item.parent
            LEFT JOIN
                `tabPurchase Receipt Item` pr_item ON pr_item.purchase_order_item = po_item.name
            LEFT JOIN
                `tabPurchase Receipt` pr ON pr.name = pr_item.parent
            LEFT JOIN
                `tabPurchase Invoice Item` pi_item ON (
                    pi_item.pr_detail = pr_item.name
                    OR pi_item.po_detail = po_item.name
                )
            LEFT JOIN
                `tabPurchase Invoice` pi ON pi.name = pi_item.parent
            WHERE
                po_item.project = %(project)s
                AND (po_item.material_request_item IS NULL OR po_item.material_request_item = '')
                AND po.docstatus < 2
        ) as combined_results
        ORDER BY
            transaction_date DESC, mr_name DESC, po_name DESC
    """

	data = frappe.db.sql(sql, {"project": project_name}, as_dict=True)

	# Post-processing to group by doctype and format data
	result = {}
	for row in data:
		progress = _line_progress(row)

		# Determine the latest stage for this procurement chain (Graduation Logic)
		stage = "Material Request"
		if row.get("pi_name"):
			stage = "Purchase Invoice"
		elif row.get("pr_name"):
			stage = "Purchase Receipt"
		elif row.get("se_name"):
			stage = "Stock Entry"
		elif row.get("po_name"):
			stage = "Purchase Order"
		elif row.get("sq_name"):
			stage = "Supplier Quotation"
		elif row.get("rfq_name"):
			stage = "Request for Quotation"

		# Special case for Subcontracting
		if stage == "Purchase Receipt" and row.get("is_subcontracted"):
			stage = "Subcontracting Receipt"

		if stage not in result:
			result[stage] = []

		source_doc_type = row.get("source_doctype")
		source_doc_name = None
		if source_doc_type == 'Material Request':
			source_doc_name = row.get('mr_name')
		elif source_doc_type == 'Purchase Order':
			source_doc_name = row.get('po_name')


		item_row = {
			"source_doc_type": source_doc_type,
			"source_doc_name": source_doc_name,
			"item_code": row.get("item_code"),
			"item_name": row.get("item_name"),
			# Stable identity for this line. The feed's own OR-join can emit the same
			# request line twice (once per Purchase Order it was split across), so an
			# array index is not an identity — it is a position.
			"mr_item": row.get("mr_item_name"),
			"po_item": row.get("po_item_name"),
			"mr": row.get("mr_name"),
			"mr_status": row.get("mr_status"),
			"rfq": row.get("rfq_name"),
			"rfq_status": row.get("rfq_status"),
			"sq": row.get("sq_name"),
			"sq_status": row.get("sq_status"),
			"po": row.get("po_name"),
			"po_status": row.get("po_status"),
			"pr": row.get("pr_name"),
			"pr_status": row.get("pr_status"),
			"pi": row.get("pi_name"),
			"pi_status": row.get("pi_status"),
			"stock_entry": row.get("se_name"),
			"stock_entry_status": row.get("se_status"),
			"warehouse": row.get("warehouse"),
			"uom": row.get("line_uom"),
			"stock_uom": row.get("line_stock_uom"),
		}
		# requested_qty / ordered_qty / received_qty / order_status / receive_status /
		# completion_percentage, all computed from THIS line's own quantities. The
		# item row no longer borrows its parent's answer.
		item_row.update(progress)
		result[stage].append(item_row)

	return result


def _line_progress(row):
	"""Per-line quantity progress for one chain row.

	Two shapes reach here. A **Material Request line** answers from ERPNext's own
	denormalized rollups on that line — per-line, so the OR-join fan-out cannot
	inflate them, and already net of amendments, cancellations and returns. A
	**direct Purchase Order line** has no request behind it, so the order is the ask.

	The two use different UOM bases on purpose; see the SELECT comments. Both are
	internally consistent, which is what matters — the ratio is always taken between
	two numbers maintained against the same basis.
	"""
	if row.get("mr_item_name"):
		return procurement_quantities.quantity_progress(
			requested=row.get("mr_requested_qty"),
			ordered=row.get("mr_ordered_qty"),
			received=row.get("mr_received_qty"),
		)

	# Direct Purchase Order line. Only a submitted order is an order; a draft is an
	# intention, reported separately so it is visible without being counted.
	line_qty = row.get("po_line_qty")
	submitted = row.get("po_docstatus") == 1
	return procurement_quantities.quantity_progress(
		requested=None,
		ordered=line_qty if submitted else 0,
		received=row.get("po_line_received_qty"),
		draft_ordered=0 if submitted else line_qty,
	)


# ---------------------------------------------------------------------------
# Document-centric procurement view (custom_material_request_feed)
#
# get_procurement_status() above is item/chain centric (grouped by the latest
# stage of each item's chain). The Project feed instead wants a document-centric
# tree: DocType -> list of documents -> expand a document -> its items (each item
# still carrying its full Doc Chain). get_procurement_documents() builds that on
# top of get_procurement_status(): the chain rows already carry every doc name in
# the chain, so we just regroup them per document, then add any documents that
# are linked to the project but never appeared in a chain.
# ---------------------------------------------------------------------------

# Fixed top-to-bottom order requested for the feed.
PROCUREMENT_DOCTYPE_ORDER = [
	"Material Request",
	"Request for Quotation",
	"Supplier Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
]

# Maps each procurement DocType to the key it occupies on a chain/item row.
_CHAIN_FIELD = {
	"Material Request": "mr",
	"Request for Quotation": "rfq",
	"Supplier Quotation": "sq",
	"Purchase Order": "po",
	"Purchase Receipt": "pr",
	"Purchase Invoice": "pi",
}

# Date field shown in the document row, per DocType.
_DATE_FIELD = {
	"Material Request": "transaction_date",
	"Request for Quotation": "transaction_date",
	"Supplier Quotation": "transaction_date",
	"Purchase Order": "transaction_date",
	"Purchase Receipt": "posting_date",
	"Purchase Invoice": "posting_date",
}

# DocTypes that carry a single supplier on the header.
_HAS_SUPPLIER = {"Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice"}


def _docstatus_label(docstatus):
	return {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(docstatus, "")


#: Stages at which a quantity on the document is a *commitment to buy*, as opposed to
#: a request or a quote. Used only for documents that never joined a chain — see
#: :func:`_minimal_item_row`.
_ORDER_STAGE_DOCTYPES = {"Purchase Order", "Purchase Receipt", "Purchase Invoice"}


def _minimal_item_row(doctype, docname, status, item):
	"""Builds an item row (same shape the feed expects) for a document that was
	not reached through the chain query. Only the document's own chain node is
	populated; upstream/downstream nodes are left blank."""
	# All that is knowable here is the document's own line quantity — by definition
	# this document is not in a chain, so there is no ordered-against-requested pair
	# to compare. Which side of the fence that quantity falls on depends on the stage:
	# a Material Request line is an ask, a Purchase Order line is an order. The old
	# code put it in `ordered_qty` for every doctype, so an untouched request rendered
	# as fully ordered here too.
	qty = item.get("qty") or 0
	if doctype in _ORDER_STAGE_DOCTYPES:
		progress = procurement_quantities.quantity_progress(requested=None, ordered=qty, received=0)
	else:
		progress = procurement_quantities.quantity_progress(requested=qty, ordered=0, received=0)

	row = {
		"source_doc_type": doctype,
		"source_doc_name": docname,
		"item_code": item.get("item_code"),
		"item_name": item.get("item_name"),
		"mr_item": None,
		"po_item": None,
		"mr": None, "mr_status": None,
		"rfq": None, "rfq_status": None,
		"sq": None, "sq_status": None,
		"po": None, "po_status": None,
		"pr": None, "pr_status": None,
		"pi": None, "pi_status": None,
		"stock_entry": None, "stock_entry_status": None,
		"warehouse": item.get("warehouse"),
		"uom": item.get("uom"),
		"stock_uom": item.get("stock_uom"),
	}
	row.update(progress)
	field = _CHAIN_FIELD[doctype]
	row[field] = docname
	row[field + "_status"] = status
	return row


def _supplementary_documents(project_name, doctype, existing_names):
	"""Finds documents of `doctype` linked to the project (via item.project or the
	parent's project/custom_project field) that are not already present, and
	returns {docname: [item_row, ...]} for them."""
	item_doctype = f"{doctype} Item"
	existing = set(existing_names)

	candidate_names = set()

	item_meta = frappe.get_meta(item_doctype)
	if item_meta.has_field("project"):
		for r in frappe.get_all(
			item_doctype, filters={"project": project_name}, fields=["parent"], distinct=True
		):
			candidate_names.add(r.parent)

	parent_meta = frappe.get_meta(doctype)
	for field in ("custom_project", "project"):
		if parent_meta.has_field(field):
			for name in frappe.get_all(doctype, filters={field: project_name}, pluck="name"):
				candidate_names.add(name)

	candidate_names -= existing
	if not candidate_names:
		return {}

	valid = frappe.get_all(
		doctype,
		filters={"name": ["in", list(candidate_names)], "docstatus": ["<", 2]},
		fields=["name", "status", "docstatus"],
	)

	item_fields = ["parent", "item_code", "item_name", "qty"]
	# Guarded per field: these child tables are not uniform, and a missing column here
	# raises rather than degrades. `_supplementary_documents` is wrapped in a bare
	# except upstream, so an unguarded read would silently drop a whole doctype group
	# from the feed rather than erroring visibly.
	for optional in ("warehouse", "uom", "stock_uom"):
		if item_meta.has_field(optional):
			item_fields.append(optional)

	result = {}
	for v in valid:
		status = v.status or _docstatus_label(v.docstatus)
		items = frappe.get_all(item_doctype, filters={"parent": v.name}, fields=item_fields)
		result[v.name] = [_minimal_item_row(doctype, v.name, status, it) for it in items]
	return result


def _document_rollup(items):
	"""Requested / ordered / received totalled across one document's item rows.

	**De-duplicates before summing, and that is the whole reason this is a function.**
	The feed's Purchase Order join matches ``supplier_quotation_item OR
	material_request_item``, so a request line reachable by both paths comes back more
	than once — on PRJ-00566 that is 19 rows for MAT-MR-2026-00001's 10 lines. A naive
	``sum(row["ordered_qty"] for row in items)`` nearly doubles the total. Keying on the
	child row name collapses the duplicates; rows with no child row of their own (the
	supplementary sweep builds those) are distinct objects and count once each.
	"""
	return procurement_quantities.rollup_quantity_progress(
		procurement_quantities.dedupe_lines(items)
	)


def _fetch_doc_meta(doctype, names):
	"""Bulk-fetches header metadata (date, supplier, status) for a set of documents."""
	if not names:
		return {}
	date_field = _DATE_FIELD[doctype]
	fields = ["name", "status", "docstatus", f"{date_field} as doc_date"]
	if doctype in _HAS_SUPPLIER:
		fields.append("supplier")
	rows = frappe.get_all(doctype, filters={"name": ["in", list(names)]}, fields=fields)
	return {r.name: r for r in rows}


@frappe.whitelist()
def get_procurement_documents(project_name):
	"""Document-centric procurement feed for a Project.

	Returns an ordered list (Material Request -> ... -> Purchase Invoice, empty
	groups omitted):

		[
			{
				"doctype": "Material Request",
				"documents": [
					{"name", "date", "supplier", "status", "items": [<item row>, ...]},
					...
				],
			},
			...
		]

	Each item row keeps the full Doc Chain fields (mr/rfq/sq/po/pr/pi + statuses).
	"""
	if not project_name:
		return []

	# 1. Flatten the chain rows (each already carries every doc name in its chain).
	status = get_procurement_status(project_name)
	chain_rows = [item for items in status.values() for item in items]

	# 2. Group every chain row under each document it belongs to.
	doc_items = {dt: {} for dt in PROCUREMENT_DOCTYPE_ORDER}
	for row in chain_rows:
		for dt, field in _CHAIN_FIELD.items():
			docname = row.get(field)
			if docname:
				doc_items[dt].setdefault(docname, []).append(row)

	# 3. Add documents linked to the project that never appeared in a chain.
	for dt in PROCUREMENT_DOCTYPE_ORDER:
		try:
			supplementary = _supplementary_documents(project_name, dt, doc_items[dt].keys())
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Procurement feed: supplementary {dt} failed")
			supplementary = {}
		for docname, items in supplementary.items():
			doc_items[dt].setdefault(docname, []).extend(items)

	# 4. Assemble the ordered output, skipping empty groups.
	output = []
	for dt in PROCUREMENT_DOCTYPE_ORDER:
		names = list(doc_items[dt].keys())
		if not names:
			continue
		meta = _fetch_doc_meta(dt, names)
		documents = []
		for name in names:
			m = meta.get(name) or {}
			items = doc_items[dt][name]
			documents.append(
				{
					"name": name,
					"date": str(m.get("doc_date")) if m.get("doc_date") else None,
					"supplier": m.get("supplier"),
					"status": m.get("status") or _docstatus_label(m.get("docstatus")),
					"items": items,
					# Totals across this document's lines, computed here rather than in
					# the browser so the MCP tool gets them too and so the de-duplication
					# happens where the child row names are known.
					"rollup": _document_rollup(items),
				}
			)
		# Newest documents first.
		documents.sort(key=lambda d: (d["date"] or "", d["name"]), reverse=True)
		output.append({"doctype": dt, "documents": documents})

	return output


def sync_attachments_from_opportunity(doc, method):
	"""
	Sync attachments from the linked Opportunity (and its parent Lead) to the Project.

	Reads the persisted ``custom_opportunity`` link (with a fallback to the
	legacy in-memory ``custom_sales_opportunity`` attribute, which only ever
	existed within the creation request — see opportunity_enhancements.py,
	v1.3.0 note). Idempotent: files already on the Project (by file_name) are
	never re-copied, so running on every save just picks up late additions.
	"""
	source_opportunity = doc.get("custom_opportunity") or doc.get("custom_sales_opportunity")
	if not source_opportunity:
		return

	# 1. Get the list of files ALREADY on this Project (prevent duplicates)
	existing_files = frappe.get_all(
		"File", filters={"attached_to_doctype": "Project", "attached_to_name": doc.name}, pluck="file_name"
	)

	# 2. Identify all related IDs (Opportunity + original Lead)
	ids_to_check = [source_opportunity]

	# We need to load the Opportunity to see if it has a parent Lead
	try:
		# Try loading as standard Opportunity
		source_opp = frappe.get_doc("Opportunity", source_opportunity)

		# Check if it is linked to a Lead (standard field 'party_name' or 'lead')
		if source_opp.opportunity_from == "Lead" and source_opp.party_name:
			ids_to_check.append(source_opp.party_name)

	except Exception:
		# If standard load fails, it might be a 'CRM Opportunity' (custom app)
		# We try to find the Lead ID from the ID string itself if possible
		pass

	# 3. Find files attached to ANY of these IDs (Opportunity OR Lead)
	# We filter ONLY by name, ignoring the DocType, to catch both Lead and Opp files
	source_files = frappe.get_all(
		"File",
		filters={"attached_to_name": ["in", ids_to_check]},
		fields=["file_name", "file_url", "is_private", "attached_to_name"],
	)

	copied_count = 0

	# 4. Copy the files
	for file in source_files:
		if file.file_name not in existing_files:
			try:
				new_file = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": file.file_name,
						"file_url": file.file_url,
						"attached_to_doctype": "Project",
						"attached_to_name": doc.name,
						"is_private": file.is_private,
					}
				)
				new_file.insert(ignore_permissions=True)
				copied_count += 1
			except Exception as e:
				frappe.log_error(f"Error copying {file.file_name}: {e!s}")

	# 5. Success Message
	if copied_count > 0:
		frappe.msgprint(f"Success: Synced {copied_count} attachments (from Opportunity/Lead).")


def send_project_start_reminders():
	"""
	Sends email reminders for projects starting today.
	Run daily at midnight.
	"""
	today = frappe.utils.nowdate()
	projects = frappe.get_all(
		"Project",
		filters={"expected_start_date": today},
		fields=["name", "project_name", "expected_start_date"],
	)

	if not projects:
		return

	settings = frappe.get_single("ERPNext Enhancements Settings")
	if not settings.project_reminder_emails:
		return

	recipients = [row.email for row in settings.project_reminder_emails if row.email]
	if not recipients:
		return

	for project in projects:
		subject = _("Project Reminder: {0} starts today").format(project.project_name or project.name)

		message = f"""
        <h3>{_("Project Reminder")}</h3>
        <p>{_("The project <b>{0}</b> is expected to start today ({1}).").format(project.project_name or project.name, project.expected_start_date)}</p>
        <p><a href="{frappe.utils.get_url_to_form('Project', project.name)}">{_("View Project")}</a></p>
        """

		frappe.sendmail(recipients=recipients, subject=subject, message=message)


def get_dashboard_data(data):
	"""
	Override Project dashboard to include documents linked via custom_project field,
	plus a Travel group (Travel Trip links Projects through its read-only
	``project`` mirror field — see travel_management).
	"""
	if not data:
		data = {}

	if "non_standard_fieldnames" not in data:
		data["non_standard_fieldnames"] = {}

	data["non_standard_fieldnames"].update(
		{
			"Material Request": "custom_project",
			"Request for Quotation": "custom_project",
			"Travel Trip": "project",
		}
	)

	data.setdefault("transactions", []).append(
		{"label": _("Travel"), "items": ["Travel Trip"]}
	)

	return data


@frappe.whitelist()
def get_project_comments(project_name):
	"""
	Retrieves standard comments for a given project.
	"""
	if not project_name:
		return []

	# Fetch standard comments linked to the project
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Project",
			"reference_name": project_name,
			"comment_type": "Comment"
		},
		fields=["name", "content", "owner", "creation"],
		order_by="creation desc"
	)

	# Get user details in bulk to avoid N+1 queries
	user_ids = [c.get("owner") for c in comments]
	if not user_ids:
		return []

	# Get user images
	user_ids = [c.owner for c in comments if c.owner]
	user_map = {}
	if user_ids:
		users = frappe.get_all(
			"User",
			filters={"name": ["in", list(set(user_ids))]},
			fields=["name", "full_name", "user_image"]
		)
		user_map = {u.name: u for u in users}

	# Combine comment data with user details
	for comment in comments:
		user = user_map.get(comment.get("owner"))
		if user:
			comment["full_name"] = user.get("full_name")
			comment["user_image"] = user.get("user_image")
		else:
			comment["full_name"] = comment.get("owner")
			comment["user_image"] = None

	return comments


@frappe.whitelist()
def add_project_comment(project_name, comment_text):
	"""
	Adds a standard comment to a project.
	"""
	if not project_name or not comment_text:
		frappe.throw(_("Project name and comment text are required."))

	doc = frappe.new_doc("Comment")
	doc.comment_type = "Comment"
	doc.reference_doctype = "Project"
	doc.reference_name = project_name
	doc.content = comment_text
	doc.insert(ignore_permissions=True)

	# Refetch the comment to include user details for the frontend
	user_details = frappe.get_doc("User", doc.owner)

	return {
		"name": doc.name,
		"content": doc.content,
		"owner": doc.owner,
		"creation": doc.creation,
		"full_name": user_details.full_name,
		"user_image": user_details.user_image
	}


@frappe.whitelist()
def delete_project_comment(project_name, comment_name):
	"""
	Deletes a comment if the user is the owner.
	"""
	if not comment_name:
		frappe.throw(_("Comment name is required."))

	try:
		comment = frappe.get_doc("Comment", comment_name)

		if comment.owner != frappe.session.user:
			frappe.throw(_("You are not allowed to delete this comment."))

		comment.delete(ignore_permissions=True)
		return {"success": True}
	except frappe.PermissionError:
		frappe.throw(_("You are not allowed to delete this comment."))
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Comment Deletion Failed")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def update_project_comment(project_name, comment_name, comment_text):
	if not comment_name or not comment_text:
		frappe.throw(_("Comment name and comment text are required."))

	try:
		comment = frappe.get_doc("Comment", comment_name)

		if comment.owner != frappe.session.user:
			frappe.throw(_("You are not allowed to edit this comment."))

		comment.content = comment_text
		comment.save(ignore_permissions=True)

		user_details = frappe.get_doc("User", comment.owner)

		return {
			"name": comment.name,
			"content": comment.content,
			"owner": comment.owner,
			"creation": comment.creation,
			"full_name": user_details.full_name,
			"user_image": user_details.user_image
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error updating project comment")
		return {"error": str(e)}
