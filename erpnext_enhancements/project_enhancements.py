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
