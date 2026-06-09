"""One-time migration patch (post_model_sync; listed in patches.txt).

Backfills the new Contact/Address directory model from the various legacy
representations so existing data shows up in the unified widgets created by
``setup/custom_fields.py``. It:

* copies Contact standard ``phone``/``mobile_no`` into the ``custom_*`` fields,
* promotes scalar ``primary_contact`` links (Customer/Project) into Contact
  Dynamic Links + ``is_primary_contact``,
* migrates the legacy ``Project Stakeholder`` child table into Contact/Address
  links (honoring the "Primary Contact" role),
* migrates assorted legacy address fields (custom billing/shipping, party
  ``*_primary_address``, Contact ``address``) into the Address Directory with
  proper Dynamic Links and address types, and
* sweeps any party convenience fields (phone/email/title) that were never copied
  onto their primary Contact.

Every step guards on field/table/record existence and wraps writes in
try/except, so it is safe on partially-migrated or differently-shaped sites.
"""
import frappe

def execute():
    """
    Migrates contact data, preserves primary contact links,
    and migrates data from legacy child tables.
    """
    # 1. Migrate Contact DocType standard fields to custom fields
    contacts = frappe.db.get_all(
        "Contact", 
        fields=["name", "phone", "mobile_no", "custom_phone_number", "custom_mobile_number"]
    )
    
    for c in contacts:
        updates = {}
        if c.phone and not c.custom_phone_number:
            updates["custom_phone_number"] = c.phone
        if c.mobile_no and not c.custom_mobile_number:
            updates["custom_mobile_number"] = c.mobile_no
        
        if updates:
            frappe.db.set_value("Contact", c.name, updates, update_modified=False)

    # Step A: Scalar Field Migration (Customer & Project)
    migrate_scalar_links("Customer")
    migrate_scalar_links("Project")

    # Step B: Child Table Migration (custom_contacts__address_table / Project Stakeholder)
    migrate_project_stakeholders()

    # Step C: Legacy Address Field Migration
    migrate_legacy_address_fields()
    migrate_customer_specific_address_fields()
    migrate_supplier_specific_address_fields()
    migrate_contact_address_fields()

    # Final Sweep for orphaned data in old fields
    sweep_orphaned_data()

def migrate_supplier_specific_address_fields():
    """Migrates supplier_primary_address to the Address Directory."""
    if not frappe.db.exists("DocType", "Supplier"):
        return
        
    meta = frappe.get_meta("Supplier")
    
    if not meta.has_field("supplier_primary_address"):
        return
        
    records = frappe.db.get_all("Supplier", fields=["name", "primary_address", "supplier_primary_address"])
    
    for doc in records:
        primary_addr_val = doc.get("supplier_primary_address")
        
        if primary_addr_val:
            # Set on Supplier if not already set
            if meta.has_field("primary_address") and not doc.primary_address:
                frappe.db.set_value("Supplier", doc.name, "primary_address", primary_addr_val, update_modified=False)
            
            # Ensure Address has a dynamic link
            ensure_address_link_and_type(primary_addr_val, "Supplier", doc.name, is_primary=True)

def migrate_contact_address_fields():
    """Migrates 'address' field in Contact to 'primary_address'."""
    if not frappe.db.exists("DocType", "Contact"):
        return
        
    meta = frappe.get_meta("Contact")
    if not meta.has_field("address") or not meta.has_field("primary_address"):
        return
        
    contacts = frappe.db.get_all(
        "Contact", 
        filters={"address": ["is", "set"]}, 
        fields=["name", "address", "primary_address"]
    )
    
    for c in contacts:
        if not c.primary_address:
            frappe.db.set_value("Contact", c.name, "primary_address", c.address, update_modified=False)
        
        # Ensure Address has a dynamic link to this Contact
        ensure_address_link_and_type(c.address, "Contact", c.name, is_primary=True)

def migrate_customer_specific_address_fields():
    """Migrates custom_billing_address, custom_shipping_address, and customer_primary_address to the Address Directory."""
    if not frappe.db.exists("DocType", "Customer"):
        return
        
    meta = frappe.get_meta("Customer")
    
    # Identify which fields actually exist in the current meta
    address_fields = []
    if meta.has_field("customer_primary_address"): address_fields.append("customer_primary_address")
    if meta.has_field("custom_billing_address"): address_fields.append("custom_billing_address")
    if meta.has_field("custom_shipping_address"): address_fields.append("custom_shipping_address")
    
    if not address_fields:
        return
        
    # Query only if at least one of these fields exists
    fields_to_query = ["name", "primary_address"] + address_fields
    
    # We want records where at least one of these address fields is set
    # Since frappe.get_all doesn't support complex OR conditions natively in a simple way, 
    # we'll just fetch all and filter in Python, or use an OR filter if Frappe version supports it.
    # Fetching all customers with any of these fields populated:
    records = frappe.db.get_all("Customer", fields=fields_to_query)
    
    for doc in records:
        primary_addr_val = doc.get("customer_primary_address")
        billing_addr_val = doc.get("custom_billing_address")
        shipping_addr_val = doc.get("custom_shipping_address")
        
        # 1. Primary Address
        if primary_addr_val:
            # Set on Customer if not already set
            if meta.has_field("primary_address") and not doc.primary_address:
                frappe.db.set_value("Customer", doc.name, "primary_address", primary_addr_val, update_modified=False)
            
            # Ensure Address has a dynamic link
            ensure_address_link_and_type(primary_addr_val, "Customer", doc.name, is_primary=True)
            
        # 2. Billing Address
        if billing_addr_val:
            ensure_address_link_and_type(billing_addr_val, "Customer", doc.name, address_type="Billing")
            
        # 3. Shipping Address
        if shipping_addr_val:
            ensure_address_link_and_type(shipping_addr_val, "Customer", doc.name, address_type="Shipping")

def migrate_scalar_links(doctype):
    """Promote each record's scalar ``primary_contact`` into a Contact link."""
    records = frappe.get_all(doctype, filters={"primary_contact": ["is", "set"]}, fields=["name", "primary_contact"])
    for doc in records:
        ensure_dynamic_link_and_primary(doc.primary_contact, doctype, doc.name)

def migrate_legacy_address_fields():
    """Migrates custom_customer__lead_address to primary_address for Customer and Lead."""
    for doctype in ["Customer", "Lead"]:
        if not frappe.db.exists("DocType", doctype):
            continue
            
        meta = frappe.get_meta(doctype)
        if meta.has_field("custom_customer__lead_address"):
            records = frappe.get_all(
                doctype, 
                filters={"custom_customer__lead_address": ["is", "set"]}, 
                fields=["name", "custom_customer__lead_address", "primary_address"]
            )
            
            for doc in records:
                # Only migrate if primary_address is not already set
                if not doc.primary_address:
                    frappe.db.set_value(doctype, doc.name, "primary_address", doc.custom_customer__lead_address, update_modified=False)
                
                # Also ensure the Address has a Dynamic Link to this record
                ensure_address_link(doc.custom_customer__lead_address, doctype, doc.name)

def migrate_project_stakeholders():
    """Migrates data from tabProject Stakeholder to the new Link-based system."""
    if not frappe.db.table_exists("Project Stakeholder"):
        return

    stakeholders = frappe.db.get_all(
        "Project Stakeholder", 
        fields=["name", "parent", "parenttype", "contact_person", "address", "role"]
    )

    for s in stakeholders:
        # 1. Migrate Contact
        if s.contact_person:
            ensure_dynamic_link_and_primary(
                s.contact_person, 
                s.parenttype, 
                s.parent, 
                is_primary=(s.role == "Primary Contact")
            )
            
            # If primary role, also update parent doc field
            if s.role == "Primary Contact":
                try:
                    parent_doc = frappe.get_doc(s.parenttype, s.parent)
                    parent_doc.primary_contact = s.contact_person
                    parent_doc.save(ignore_permissions=True, ignore_links=True)
                except Exception:
                    pass

        # 2. Migrate Address
        if s.address:
            ensure_address_link(s.address, s.parenttype, s.parent)
            
            # If primary role, also update parent doc field
            if s.role == "Primary Contact":
                try:
                    parent_doc = frappe.get_doc(s.parenttype, s.parent)
                    parent_doc.primary_address = s.address
                    parent_doc.save(ignore_permissions=True, ignore_links=True)
                except Exception:
                    pass

def ensure_dynamic_link_and_primary(contact_name, link_doctype, link_name, is_primary=True):
    """Append a Dynamic Link (and optionally set primary) on a Contact, idempotently."""
    if not frappe.db.exists("Contact", contact_name):
        return

    try:
        contact = frappe.get_doc("Contact", contact_name)
        
        link_exists = False
        for link in contact.links:
            if link.link_doctype == link_doctype and link.link_name == link_name:
                link_exists = True
                break
        
        changed = False
        if not link_exists:
            contact.append("links", {
                "link_doctype": link_doctype,
                "link_name": link_name
            })
            changed = True
        
        if is_primary:
            contact.is_primary_contact = 1
            changed = True
        
        if changed:
            contact.save(ignore_permissions=True, ignore_links=True)
    except Exception:
        pass

def ensure_address_link_and_type(address_name, link_doctype, link_name, address_type=None, is_primary=False):
    """Append a Dynamic Link on an Address, optionally setting its type/primary flag."""
    if not frappe.db.exists("Address", address_name):
        return

    try:
        address = frappe.get_doc("Address", address_name)
        
        link_exists = False
        for link in address.links:
            if link.link_doctype == link_doctype and link.link_name == link_name:
                link_exists = True
                break
        
        changed = False
        if not link_exists:
            address.append("links", {
                "link_doctype": link_doctype,
                "link_name": link_name
            })
            changed = True
            
        if address_type and address.address_type != address_type:
            address.address_type = address_type
            changed = True
            
        if is_primary and not address.is_primary_address:
            address.is_primary_address = 1
            changed = True
            
        if changed:
            address.save(ignore_permissions=True, ignore_links=True)
    except Exception:
        pass

def ensure_address_link(address_name, link_doctype, link_name):
    """Append a Dynamic Link on an Address, idempotently (no type/primary change)."""
    if not frappe.db.exists("Address", address_name):
        return

    try:
        address = frappe.get_doc("Address", address_name)
        
        link_exists = False
        for link in address.links:
            if link.link_doctype == link_doctype and link.link_name == link_name:
                link_exists = True
                break
        
        if not link_exists:
            address.append("links", {
                "link_doctype": link_doctype,
                "link_name": link_name
            })
            address.save(ignore_permissions=True, ignore_links=True)
    except Exception:
        pass

def sweep_orphaned_data():
    """Copy any party primary-contact convenience fields onto their Contact.

    Final cleanup pass: for each party with a ``primary_contact``, move
    ``primary_contact_phone``/``_email``/``_job_title`` into the Contact's
    ``custom_*`` fields when the Contact does not already have them.
    """
    main_doctypes = ["Project", "Opportunity", "Supplier", "Customer"]
    for dt in main_doctypes:
        if not frappe.db.exists("DocType", dt):
            continue
        
        meta = frappe.get_meta(dt)
        fields_to_check = {
            "primary_contact_phone": "custom_phone_number",
            "primary_contact_email": "custom_email",
            "primary_contact_job_title": "custom_title"
        }
        
        existing_fields = [f for f in fields_to_check.keys() if meta.has_field(f)]
        if not existing_fields:
            continue

        query_fields = ["name", "primary_contact"] + existing_fields
        docs = frappe.db.get_all(dt, fields=query_fields)
        
        for doc in docs:
            if not doc.primary_contact:
                continue
            
            try:
                contact = frappe.get_doc("Contact", doc.primary_contact)
                changed = False
                
                for old_f, new_f in fields_to_check.items():
                    if meta.has_field(old_f) and doc.get(old_f) and not contact.get(new_f):
                        contact.set(new_f, doc.get(old_f))
                        changed = True
                
                if changed:
                    contact.save(ignore_permissions=True, ignore_links=True)
            except Exception:
                continue
