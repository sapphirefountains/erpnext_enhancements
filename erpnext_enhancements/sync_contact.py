import frappe

PRIMARY_CONTACT_DOCTYPES = ["Project", "Opportunity", "Supplier", "Customer"]

@frappe.whitelist()
def set_primary_contact(account_doctype, account_name, contact_name):
    # Find all contacts linked to this account context
    linked_contacts = frappe.get_all(
        "Dynamic Link", 
        filters={
            "link_doctype": account_doctype, 
            "link_name": account_name, 
            "parenttype": "Contact"
        }, 
        pluck="parent"
    )
    
    if linked_contacts:
        # Uncheck is_primary_contact for all of them
        frappe.db.set_value("Contact", {"name": ["in", linked_contacts]}, "is_primary_contact", 0)

    # Check the new one
    frappe.db.set_value("Contact", contact_name, "is_primary_contact", 1)

@frappe.whitelist()
def set_primary_address(account_doctype, account_name, address_name):
    # Find all addresses linked to this account context
    linked_addresses = frappe.get_all(
        "Dynamic Link", 
        filters={
            "link_doctype": account_doctype, 
            "link_name": account_name, 
            "parenttype": "Address"
        }, 
        pluck="parent"
    )
    
    if linked_addresses:
        # Uncheck is_primary_address for all of them
        frappe.db.set_value("Address", {"name": ["in", linked_addresses]}, "is_primary_address", 0)

    # Check the new one
    frappe.db.set_value("Address", address_name, "is_primary_address", 1)

@frappe.whitelist()
def link_existing_record(doctype, docname, link_doctype=None, link_name=None, links=None):
    """Links an existing Contact or Address to a document(s)."""
    import json
    doc = frappe.get_doc(doctype, docname)
    
    links_to_add = []
    if links:
        if isinstance(links, str):
            links_to_add = json.loads(links)
        else:
            links_to_add = links
    elif link_doctype and link_name:
        links_to_add = [{"link_doctype": link_doctype, "link_name": link_name}]
        
    changed = False
    existing_links = set((l.link_doctype, l.link_name) for l in doc.links)
    
    for l in links_to_add:
        if (l.get("link_doctype"), l.get("link_name")) not in existing_links:
            doc.append("links", {
                "link_doctype": l.get("link_doctype"),
                "link_name": l.get("link_name")
            })
            changed = True
            
    if changed:
        doc.save(ignore_permissions=True)
    return True

@frappe.whitelist()
def unlink_record(doctype, docname, link_doctype, link_name):
    """Unlinks a Contact or Address from a specific document."""
    doc = frappe.get_doc(doctype, docname)
    
    new_links = []
    found = False
    for l in doc.links:
        if l.link_doctype == link_doctype and l.link_name == link_name:
            found = True
            continue
        new_links.append({
            "link_doctype": l.link_doctype,
            "link_name": l.link_name
        })
            
    if found:
        doc.set("links", new_links)
        doc.save(ignore_permissions=True)
        return True
    return False

@frappe.whitelist()
def get_contacts_for_context(sources):
    import json
    if isinstance(sources, str):
        sources = json.loads(sources)
        
    source_names = [s.get("name") for s in sources]
    if not source_names:
        return []
        
    contacts = frappe.get_all(
        "Contact",
        filters=[["Dynamic Link", "link_name", "in", source_names]],
        fields=["name", "first_name", "last_name", "custom_title", "custom_phone_number", "custom_mobile_number", "custom_email", "is_primary_contact"]
    )
    
    unique_contacts = {c.name: c for c in contacts}
    contact_list = list(unique_contacts.values())
    
    if not contact_list:
        return []
        
    links = frappe.get_all(
        "Dynamic Link",
        filters={"parent": ["in", list(unique_contacts.keys())], "parenttype": "Contact"},
        fields=["parent", "link_doctype", "link_name"]
    )
    
    link_map = {}
    for l in links:
        if l.parent not in link_map:
            link_map[l.parent] = []
        link_map[l.parent].append({"name": l.link_name, "doctype": l.link_doctype})
        
    for c in contact_list:
        c.links = link_map.get(c.name, [])
        
    return contact_list

@frappe.whitelist()
def get_addresses_for_context(sources):
    import json
    if isinstance(sources, str):
        sources = json.loads(sources)
        
    source_names = [s.get("name") for s in sources]
    if not source_names:
        return []
        
    addresses = frappe.get_all(
        "Address",
        filters=[["Dynamic Link", "link_name", "in", source_names]],
        fields=["name", "address_title", "address_type", "address_line1", "address_line2", "city", "state", "pincode", "country", "is_primary_address", "custom_full_address"]
    )
    
    unique_addresses = {a.name: a for a in addresses}
    address_list = list(unique_addresses.values())
    
    if not address_list:
        return []
        
    links = frappe.get_all(
        "Dynamic Link",
        filters={"parent": ["in", list(unique_addresses.keys())], "parenttype": "Address"},
        fields=["parent", "link_doctype", "link_name"]
    )
    
    link_map = {}
    for l in links:
        if l.parent not in link_map:
            link_map[l.parent] = []
        link_map[l.parent].append({"name": l.link_name, "doctype": l.link_doctype})
        
    for a in address_list:
        a.links = link_map.get(a.name, [])
        
    return address_list

def sync_from_main_doc(doc, method):
    if not getattr(doc, "primary_contact", None):
        return

    is_new = getattr(doc, "is_new", None)
    if not (callable(is_new) and is_new()) and not (isinstance(is_new, bool) and is_new):
        old_doc = doc.get_doc_before_save()
        if old_doc and old_doc.primary_contact != doc.primary_contact:
            return

    try:
        contact = frappe.get_doc("Contact", doc.primary_contact)
    except frappe.DoesNotExistError:
        return

    changed = False

    # Sync Title
    title = getattr(doc, "primary_contact_job_title", None)
    if title is not None and (contact.custom_title or "") != title:
        contact.custom_title = title
        changed = True

    # Sync Phone
    phone = getattr(doc, "primary_contact_phone", None)
    if phone is not None and (contact.custom_phone_number or "") != phone:
        if phone: # Prevent wiping out contact data during transition
            contact.custom_phone_number = phone
            changed = True

    # Sync Email
    email = getattr(doc, "primary_contact_email", None)
    if email is not None and (contact.custom_email or "") != email:
        if email: # Prevent wiping out contact data during transition
            contact.custom_email = email
            changed = True

    if changed:
        contact.flags.ignore_permissions = True
        contact.flags.ignore_links = True
        contact.flags.is_syncing = True
        contact.save()

def sync_from_contact(doc, method):
    if getattr(doc.flags, "is_syncing", False):
        return

    custom_title = doc.custom_title or ""
    custom_phone = doc.custom_phone_number or ""
    custom_mobile = doc.custom_mobile_number or ""
    custom_email = doc.custom_email or ""
    
    phone_to_sync = custom_phone or custom_mobile

    for dt in PRIMARY_CONTACT_DOCTYPES:
        linked_docs = frappe.get_all(dt, filters={"primary_contact": doc.name})
        for linked in linked_docs:
            main_doc = frappe.get_doc(dt, linked.name)

            main_changed = False
            if hasattr(main_doc, "primary_contact_job_title") and main_doc.primary_contact_job_title != custom_title:
                main_doc.primary_contact_job_title = custom_title
                main_changed = True
            if hasattr(main_doc, "primary_contact_phone") and main_doc.primary_contact_phone != phone_to_sync:
                main_doc.primary_contact_phone = phone_to_sync
                main_changed = True
            if hasattr(main_doc, "primary_contact_email") and main_doc.primary_contact_email != custom_email:
                main_doc.primary_contact_email = custom_email
                main_changed = True

            if main_changed:
                main_doc.flags.ignore_permissions = True
                main_doc.save()
