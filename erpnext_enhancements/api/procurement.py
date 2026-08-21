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
