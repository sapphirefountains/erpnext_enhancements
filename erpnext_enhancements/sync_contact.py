import frappe

PRIMARY_CONTACT_DOCTYPES = ["Project", "Opportunity", "Supplier", "Customer"]

EXCLUSION_DOCTYPE = "Directory Link Exclusion"


def _get_excluded_names(source_doctype, source_name, ref_doctype):
    """Returns the set of Contact/Address names hidden from a given document's directory.

    Exclusions are scoped to a single source document (e.g. a specific Project),
    so a Contact unlinked from one Project still appears on the Customer it
    remains linked to.
    """
    if not source_doctype or not source_name:
        return set()

    rows = frappe.get_all(
        EXCLUSION_DOCTYPE,
        filters={
            "source_doctype": source_doctype,
            "source_name": source_name,
            "ref_doctype": ref_doctype,
        },
        pluck="ref_name",
    )
    return set(rows)


def _add_exclusion(source_doctype, source_name, ref_doctype, ref_name):
    """Records that a Contact/Address should be hidden from a source document's directory."""
    if not (source_doctype and source_name and ref_doctype and ref_name):
        return

    if frappe.db.exists(
        EXCLUSION_DOCTYPE,
        {
            "source_doctype": source_doctype,
            "source_name": source_name,
            "ref_doctype": ref_doctype,
            "ref_name": ref_name,
        },
    ):
        return

    frappe.get_doc(
        {
            "doctype": EXCLUSION_DOCTYPE,
            "source_doctype": source_doctype,
            "source_name": source_name,
            "ref_doctype": ref_doctype,
            "ref_name": ref_name,
        }
    ).insert(ignore_permissions=True)


def cleanup_directory_exclusions(doc, method=None):
    """Removes any Directory Link Exclusion rows that reference a deleted document.

    Wired to the ``on_trash`` event of Contact/Address (the referenced records)
    and of the party doctypes (the sources). The exclusion stores its references
    as plain Data, so this is the mechanism that keeps the table tidy.
    """
    exclusions = frappe.get_all(
        EXCLUSION_DOCTYPE,
        filters={"ref_doctype": doc.doctype, "ref_name": doc.name},
        pluck="name",
    ) + frappe.get_all(
        EXCLUSION_DOCTYPE,
        filters={"source_doctype": doc.doctype, "source_name": doc.name},
        pluck="name",
    )

    for excl in set(exclusions):
        frappe.delete_doc(EXCLUSION_DOCTYPE, excl, ignore_permissions=True, force=True)


def _remove_exclusion(source_doctype, source_name, ref_doctype, ref_name):
    """Clears any exclusion so a re-linked Contact/Address shows up again."""
    if not (source_doctype and source_name and ref_doctype and ref_name):
        return

    for excl in frappe.get_all(
        EXCLUSION_DOCTYPE,
        filters={
            "source_doctype": source_doctype,
            "source_name": source_name,
            "ref_doctype": ref_doctype,
            "ref_name": ref_name,
        },
        pluck="name",
    ):
        frappe.delete_doc(EXCLUSION_DOCTYPE, excl, ignore_permissions=True)

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
        link_dt = l.get("link_doctype")
        link_nm = l.get("link_name")
        if (link_dt, link_nm) not in existing_links:
            doc.append("links", {
                "link_doctype": link_dt,
                "link_name": link_nm
            })
            changed = True
        # Re-linking clears any prior "hidden from this directory" exclusion so
        # the record shows up again where it was added.
        _remove_exclusion(link_dt, link_nm, doctype, docname)

    if changed:
        doc.save(ignore_permissions=True)
    return True

@frappe.whitelist()
def unlink_record(doctype, docname, link_doctype, link_name):
    """Unlinks a Contact or Address from a specific document's directory.

    This only affects the document the user is viewing (``link_doctype`` /
    ``link_name``). It never removes the record's link to the Customer/Account or
    any other party, so the Contact/Address is never orphaned:

    * If the record is linked *directly* to this document, that one Dynamic Link
      row is removed.
    * An exclusion is recorded so the record also disappears from this
      document's directory even when it is still surfaced indirectly (e.g. a
      Contact inherited from the linked Customer's aggregated list).
    """
    doc = frappe.get_doc(doctype, docname)

    new_links = []
    removed_direct_link = False
    for l in doc.links:
        if l.link_doctype == link_doctype and l.link_name == link_name:
            removed_direct_link = True
            continue
        new_links.append({
            "link_doctype": l.link_doctype,
            "link_name": l.link_name
        })

    if removed_direct_link:
        doc.set("links", new_links)
        doc.save(ignore_permissions=True)

    # Hide it from this document's directory regardless of how it was surfaced,
    # while preserving every remaining link (Customer, Supplier, etc.).
    _add_exclusion(link_doctype, link_name, doctype, docname)

    return True

@frappe.whitelist()
def get_contacts_for_context(sources, context_doctype=None, context_name=None):
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

    # Drop contacts the user has unlinked from this specific document's directory.
    excluded = _get_excluded_names(context_doctype, context_name, "Contact")
    contact_list = [c for c in unique_contacts.values() if c.name not in excluded]

    if not contact_list:
        return []
        
    links = frappe.get_all(
        "Dynamic Link",
        filters={"parent": ["in", [c.name for c in contact_list]], "parenttype": "Contact"},
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
def get_addresses_for_context(sources, context_doctype=None, context_name=None):
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

    # Drop addresses the user has unlinked from this specific document's directory.
    excluded = _get_excluded_names(context_doctype, context_name, "Address")
    address_list = [a for a in unique_addresses.values() if a.name not in excluded]

    if not address_list:
        return []

    links = frappe.get_all(
        "Dynamic Link",
        filters={"parent": ["in", [a.name for a in address_list]], "parenttype": "Address"},
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
    if title is not None and (getattr(contact, "custom_title", None) or "") != title:
        contact.custom_title = title
        changed = True

    # Sync Phone
    phone = getattr(doc, "primary_contact_phone", None)
    if phone is not None and (getattr(contact, "custom_phone_number", None) or "") != phone:
        if phone: # Prevent wiping out contact data during transition
            contact.custom_phone_number = phone
            changed = True

    # Sync Email
    email = getattr(doc, "primary_contact_email", None)
    if email is not None and (getattr(contact, "custom_email", None) or "") != email:
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

    custom_title = getattr(doc, "custom_title", None) or ""
    custom_phone = getattr(doc, "custom_phone_number", None) or ""
    custom_mobile = getattr(doc, "custom_mobile_number", None) or ""
    custom_email = getattr(doc, "custom_email", None) or ""
    
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
