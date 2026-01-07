import frappe
from frappe import _


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
                mr.name as mr_name,
                mr.transaction_date as transaction_date,
                mr.status as mr_status,
                rfq.name as rfq_name,
                rfq.status as rfq_status,
                sq.name as sq_name,
                sq.status as sq_status,
                po.name as po_name,
                po.status as po_status,
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
                po_item.qty as ordered_qty,
                COALESCE(pr_item.qty, sed.qty) as received_qty,
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
                NULL as mr_name,
                po.transaction_date as transaction_date,
                NULL as mr_status,
                NULL as rfq_name,
                NULL as rfq_status,
                NULL as sq_name,
                NULL as sq_status,
                po.name as po_name,
                po.status as po_status,
                pr.name as pr_name,
                pr.status as pr_status,
                pi.name as pi_name,
                pi.status as pi_status,
                NULL as se_name,
                NULL as se_status,
                pr_item.warehouse as warehouse,
                po_item.qty as ordered_qty,
                pr_item.qty as received_qty,
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
		ordered_qty = row.get("ordered_qty") or 0
		mr_qty = row.get("mr_qty") or 0
		display_ordered_qty = ordered_qty if ordered_qty > 0 else mr_qty
		received_qty = row.get("received_qty") or 0
		completion_percentage = 0
		if display_ordered_qty > 0:
			completion_percentage = (received_qty / display_ordered_qty) * 100

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


		result[stage].append(
			{
				"source_doc_type": source_doc_type,
				"source_doc_name": source_doc_name,
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
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
				"ordered_qty": display_ordered_qty,
				"received_qty": received_qty,
				"completion_percentage": round(completion_percentage, 2),
			}
		)

	return result


def sync_attachments_from_opportunity(doc, method):
	"""
	Sync attachments from the linked Opportunity (and its parent Lead) to the Project.
	"""
	if not doc.custom_sales_opportunity:
		return

	# 1. Get the list of files ALREADY on this Project (prevent duplicates)
	existing_files = frappe.get_all(
		"File", filters={"attached_to_doctype": "Project", "attached_to_name": doc.name}, pluck="file_name"
	)

	# 2. Identify all related IDs (Opportunity + original Lead)
	ids_to_check = [doc.custom_sales_opportunity]

	# We need to load the Opportunity to see if it has a parent Lead
	try:
		# Try loading as standard Opportunity
		source_opp = frappe.get_doc("Opportunity", doc.custom_sales_opportunity)

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
	Override Project dashboard to include documents linked via custom_project field.
	"""
	if not data:
		data = {}

	if "non_standard_fieldnames" not in data:
		data["non_standard_fieldnames"] = {}

	data["non_standard_fieldnames"].update(
		{"Material Request": "custom_project", "Request for Quotation": "custom_project"}
	)

	return data


@frappe.whitelist()
def get_project_comments(project_name):
	"""
	Retrieves comments for a given project, along with user details.
	"""
	if not project_name:
		return []

	comments = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "Project", "reference_name": project_name},
		fields=["name", "content", "owner", "creation"],
		order_by="creation desc",
	)

	# Get user details in bulk to avoid N+1 queries
	user_ids = [c.get("owner") for c in comments]
	if not user_ids:
		return []

	user_details = frappe.get_all(
		"User",
		filters={"name": ["in", user_ids]},
		fields=["name", "full_name", "user_image"],
	)
	user_map = {u.get("name"): u for u in user_details}

	# Combine comment data with user details
	for comment in comments:
		user = user_map.get(comment.get("owner"))
		if user:
			comment["full_name"] = user.get("full_name")
			comment["user_image"] = user.get("user_image")

	return comments


@frappe.whitelist()
def add_project_comment(project_name, comment_text):
	"""
	Adds a new comment to a project.
	"""
	if not project_name or not comment_text:
		frappe.throw(_("Project name and comment text are required."))

	comment = frappe.new_doc("Comment")
	comment.reference_doctype = "Project"
	comment.reference_name = project_name
	comment.content = comment_text
	comment.insert(ignore_permissions=True)  # Assuming project members can comment

	# Refetch the comment to include user details for the frontend
	new_comment_data = frappe.get_all(
		"Comment",
		filters={"name": comment.name},
		fields=["name", "content", "owner", "creation"],
	)[0]

	user_details = frappe.get_doc("User", new_comment_data.get("owner"))
	new_comment_data["full_name"] = user_details.full_name
	new_comment_data["user_image"] = user_details.user_image

	return new_comment_data


@frappe.whitelist()
def delete_project_comment(comment_name):
	"""
	Deletes a comment if the user is the owner.
	"""
	if not comment_name:
		frappe.throw(_("Comment name is required."))

	try:
		frappe.delete_doc("Comment", comment_name, ignore_permissions=False)
		return {"success": True}
	except frappe.PermissionError:
		frappe.throw(_("You are not allowed to delete this comment."))
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Comment Deletion Failed")
		return {"success": False, "error": str(e)}
