import frappe
from frappe import _

@frappe.whitelist()
def get_procurement_status(project_name):
    """
    Fetches the procurement status for items linked to a Project.
    Trace: Material Request -> RFQ -> SQ -> PO -> PR -> PI
    """
    if not project_name:
        return []

    # Fetch Material Request Items linked to the project
    # We use a raw SQL query for better performance with multiple joins
    # Note: This assumes the standard flow. Variations might exist.
    
    sql = """
        SELECT
            mr_item.item_code,
            mr_item.item_name,
            mr_item.qty as mr_qty,
            mr.name as mr_name,
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
            pr_item.warehouse as warehouse,
            po_item.qty as ordered_qty,
            pr_item.qty as received_qty
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
        WHERE
            mr.name IN (
                SELECT parent FROM `tabMaterial Request Item` WHERE project = %(project)s
            )
            AND mr.docstatus < 2
        ORDER BY
            mr.transaction_date DESC, mr.name DESC
    """
    
    data = frappe.db.sql(sql, {"project": project_name}, as_dict=True)
    
    # Post-processing to calculate percentages and format data
    result = []
    for row in data:
        ordered_qty = row.get('ordered_qty') or 0
        mr_qty = row.get('mr_qty') or 0
        
        # Use Material Request Qty if no PO Qty (Draft/Pending stage)
        display_ordered_qty = ordered_qty if ordered_qty > 0 else mr_qty
        received_qty = row.get('received_qty') or 0
        
        completion_percentage = 0
        if display_ordered_qty > 0:
            completion_percentage = (received_qty / display_ordered_qty) * 100
            # Cap at 100% for display if over-received (unless user wants to see >100%)
            # Usually >100% is possible, but for status bar/color it might be capped.
            # We will keep the actual percentage but handle styling in JS.
        
        # Handle partial shipments:
        # The SQL above might return multiple rows for the same MR Item if there are multiple PRs for a PO.
        # However, the requirement asks to "trace the lineage".
        # If we have multiple PRs for one PO, the Left Join will duplicate the PO details.
        # For a simple list, this is acceptable as it shows each "receipt event".
        # If we wanted to aggregate, we would need to group by MR Item.
        # Given the requirement "Flow to Track", showing the individual chain is likely desired.
        
        result.append({
            'item_code': row.get('item_code'),
            'item_name': row.get('item_name'),
            'mr': row.get('mr_name'),
            'mr_status': row.get('mr_status'),
            'rfq': row.get('rfq_name'),
            'rfq_status': row.get('rfq_status'),
            'sq': row.get('sq_name'),
            'sq_status': row.get('sq_status'),
            'po': row.get('po_name'),
            'po_status': row.get('po_status'),
            'pr': row.get('pr_name'),
            'pr_status': row.get('pr_status'),
            'pi': row.get('pi_name'),
            'pi_status': row.get('pi_status'),
            'warehouse': row.get('warehouse'),
            'ordered_qty': display_ordered_qty,
            'received_qty': received_qty,
            'completion_percentage': round(completion_percentage, 2)
        })
        
    return result

def sync_attachments_from_opportunity(doc, method):
    """
    Sync attachments from the linked Opportunity (and its parent Lead) to the Project.
    """
    if not doc.custom_sales_opportunity:
        return

    # 1. Get the list of files ALREADY on this Project (prevent duplicates)
    existing_files = frappe.get_all("File", 
        filters={
            "attached_to_doctype": "Project",
            "attached_to_name": doc.name
        },
        pluck="file_name"
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
    source_files = frappe.get_all("File", 
        filters={
            "attached_to_name": ["in", ids_to_check]
        },
        fields=["file_name", "file_url", "is_private", "attached_to_name"]
    )

    copied_count = 0

    # 4. Copy the files
    for file in source_files:
        if file.file_name not in existing_files:
            try:
                new_file = frappe.get_doc({
                    "doctype": "File",
                    "file_name": file.file_name,
                    "file_url": file.file_url,
                    "attached_to_doctype": "Project",
                    "attached_to_name": doc.name,
                    "is_private": file.is_private
                })
                new_file.insert(ignore_permissions=True)
                copied_count += 1
            except Exception as e:
                frappe.log_error(f"Error copying {file.file_name}: {str(e)}")

    # 5. Success Message
    if copied_count > 0:
        frappe.msgprint(f"Success: Synced {copied_count} attachments (from Opportunity/Lead).")


def send_project_start_reminders():
    """
    Sends email reminders for projects starting today.
    Run daily at midnight.
    """
    today = frappe.utils.nowdate()
    projects = frappe.get_all("Project", filters={"expected_start_date": today}, fields=["name", "project_name", "expected_start_date"])

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
