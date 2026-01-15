import frappe

@frappe.whitelist()
def get_item_links(item_codes, supplier=None):
    """
    Fetches purchase URLs for a list of items.
    If 'supplier' is provided (e.g. on a PO), filters to that supplier.
    """
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
        if not link.purchase_url: continue
        
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
    if not url: return

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
