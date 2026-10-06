"""Contact/Address directory + primary-contact denormalization.

This module powers two related features for the party doctypes (Project,
Opportunity, Supplier, Customer — plus Master Project for the directory only):

1. **Primary-contact denormalization** — each party carries a ``primary_contact``
   Link plus convenience fields (``primary_contact_phone`` / ``_email`` /
   ``_job_title``). These are kept in two-way sync with the linked Contact's own
   ``custom_*`` fields so editing either side updates the other:

   * :func:`sync_from_main_doc` is wired to the ``on_update`` doc_event of
     Project / Opportunity / Supplier / Customer and pushes the party's
     convenience fields *down* onto the Contact.
   * :func:`sync_from_contact` is wired to Contact ``on_update`` and pushes the
     Contact's ``custom_*`` fields *up* onto every party that names it as
     primary. An ``is_syncing`` flag set by the former breaks the feedback loop.

2. **Directory + per-document exclusions** — a document's "Contact/Address
   Directory" aggregates every Contact/Address Dynamic-Link-ed to it (and, for a
   party, to its related records). Because a Contact may be surfaced *indirectly*
   (e.g. inherited from a linked Customer), a user "unlinking" it from one
   document cannot simply delete a link without orphaning it elsewhere. Instead
   a ``Directory Link Exclusion`` row records "hide ref X from source Y", scoped
   to a single source document. :func:`cleanup_directory_exclusions` is wired to
   the ``on_trash`` event of both the parties and Contact/Address to garbage
   collect exclusion rows referencing deleted documents (references are stored as
   plain Data, so there is no automatic cascade).

The whitelisted functions below back the in-form directory widget (list, link,
unlink, set-primary, and the bulk **import** pair :func:`get_importable_contacts`
/ :func:`import_contacts`, which offer a related party's Contacts with tick
boxes and link exactly the ones chosen).

**Permissions.** Every one of them is login-only, and Frappe v16's whitelist refuses
nothing but a Guest, so each body carries its own gate (see "Directory permissions"
below for the rule; until v1.561.1 the list, link and unlink endpoints had none).
"""
import frappe

# Party doctypes that carry the denormalized primary-contact fields.
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

#: The only doctypes that own the ACCOUNT-WIDE primary flags.
#:
#: ``Contact.is_primary_contact`` and ``Address.is_primary_address`` are columns on
#: the Contact/Address record, not on the ``Dynamic Link`` row, so setting one is a
#: statement about a whole account and there is nowhere in that scheme to say
#: "primary for this Project". Project / Opportunity / Master Project record their
#: primary in their own ``primary_contact`` / ``primary_address`` Link field
#: (``setup/custom_fields.py``) and must never reach these endpoints.
#:
#: Until v1.198.0 the directory widget derived the account from
#: ``frm.doc.customer`` — so a "Set Primary" click on a **Project** passed that
#: Project's Customer here, and one project-level decision rewrote a company-level
#: fact. This guard is what makes that unrepresentable rather than merely unwritten.
GLOBAL_PRIMARY_PARTY_DOCTYPES = ("Customer", "Supplier")


def _assert_account(account_doctype, account_name):
    """Reject a non-account context, and require write permission on the account.

    Both endpoints were whitelisted with no permission check of any kind, so any
    logged-in user could re-point any customer's primary contact.

    Returns the account's name as text (:func:`_docname`), the value the caller then
    queries with, so the account that was checked is the account that is matched.
    """
    if account_doctype not in GLOBAL_PRIMARY_PARTY_DOCTYPES:
        # frappe._ rather than a module-level `from frappe import _`: this module has
        # only ever imported `frappe`, and adding the name would be a wider change than
        # the one line that needs it.
        frappe.throw(
            frappe._(
                "{0} stores its primary contact and address on the document itself. "
                "Only {1} carry the account-wide primary flag."
            ).format(account_doctype, " / ".join(GLOBAL_PRIMARY_PARTY_DOCTYPES)),
            title=frappe._("Not an account"),
        )
    account_name = _require_docname(account_name, account_doctype)
    frappe.has_permission(account_doctype, "write", doc=account_name, throw=True)
    return account_name


@frappe.whitelist()
def set_primary_contact(account_doctype, account_name, contact_name):
    """Mark one Contact as primary for an account, unsetting the others.

    Clears ``is_primary_contact`` on every Contact dynamically linked to the
    given account (``account_doctype`` / ``account_name``), then sets it on
    ``contact_name``. Called from the directory widget.

    Only Customer and Supplier are accounts — see
    :data:`GLOBAL_PRIMARY_PARTY_DOCTYPES`.

    **Permissions:** write on the account, and write on the Contact being flagged
    (v1.561.1), both before anything is written. ``contact_name`` must be one name:
    ``frappe.db.set_value`` reads a dict or a list in that place as filters and updates
    every row they match, so until v1.561.1 one call with write on one account could
    flag every Contact on the site, readable or not.
    """
    account_name = _assert_account(account_doctype, account_name)
    contact_name = _require_docname(contact_name, "Contact")
    frappe.has_permission("Contact", "write", doc=contact_name, throw=True)

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
    """Mark one Address as primary for an account, unsetting the others.

    Address counterpart of :func:`set_primary_contact`, same account restriction and
    the same permissions: write on the account and on the Address being flagged, which
    must be one name.
    """
    account_name = _assert_account(account_doctype, account_name)
    address_name = _require_docname(address_name, "Address")
    frappe.has_permission("Address", "write", doc=address_name, throw=True)

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


def sync_employee_phone_to_user(doc, method=None):
    """Employee ``on_update`` doc_event: keep the linked User's ``phone`` equal
    to the Employee's Cell Number, so "Call via Triton"
    (``telephony.trigger_outbound_call``) can resolve the rep's number from
    either record. One-way — Employee is the source of truth; erpnext core's
    ``Employee.update_user`` syncs name/DOB/image but not the phone.
    """
    user_id = (doc.get("user_id") or "").strip()
    cell = (doc.get("cell_number") or "").strip()
    if not user_id or not cell:
        return
    if not frappe.db.exists("User", user_id):
        return
    if (frappe.db.get_value("User", user_id, "phone") or "") != cell:
        frappe.db.set_value("User", user_id, "phone", cell, update_modified=False)


def set_supplier_primary_address_display(doc, method=None):
    """Supplier ``validate`` doc_event: show the linked Address's
    ``custom_full_address`` as the read-only Primary Address text.

    Stock erpnext fills ``Supplier.primary_address`` (a read-only Text Editor
    display, NOT the Link — that's ``supplier_primary_address``) from the
    address template via ``get_address_display``. This site's canonical
    one-line address lives in ``Address.custom_full_address``, so prefer it;
    when the custom field is empty the stock template text is left alone.
    """
    if not doc.get("supplier_primary_address"):
        return
    full_address = frappe.db.get_value(
        "Address", doc.supplier_primary_address, "custom_full_address"
    )
    if full_address:
        doc.primary_address = full_address


def sanitize_primary_address_link(doc, method=None):
    """``before_validate`` doc_event: drop a ``primary_address`` value that isn't a
    real Address docname.

    On Opportunity / Project / Master Project (see hooks.py) ``primary_address`` is
    a **Link** to Address. Legacy data — Zoho import / an older migration — left the
    rendered address *display* (HTML, e.g. ``2600 Taylorsville BLVD<br>…``) in some
    of these rows instead of an Address name. A Link field can't validate that, so
    the record throws ``Could not find Address: …`` on every save and becomes
    un-editable. This clears any non-resolvable value before validation so the doc
    saves cleanly; the user can re-pick the primary address from the directory UI.

    Only applies where ``primary_address`` is a Link — never to Customer/Supplier,
    where it's a read-only Text Editor *display* and HTML is expected.
    """
    value = doc.get("primary_address")
    if not value:
        return
    df = doc.meta.get_field("primary_address")
    if not df or df.fieldtype != "Link":
        return
    if not frappe.db.exists("Address", value):
        doc.primary_address = None


# ---------------------------------------------------------------------------
# Directory permissions
#
# The directory endpoints are login-only, and Frappe v16's whitelist refuses nothing
# but a Guest (``is_whitelisted``, frappe/__init__.py:479-487 at v16.35.0), so the gate
# has to be in each body. Until v1.561.1 four of them had none: any signed-in user,
# portal users included, could read any party's contacts and addresses by naming the
# party, and link or unlink any Contact or Address. The rule, stated once:
#
# * a party whose contacts or addresses a call RETURNS needs **read** on that party;
# * a party a returned row merely NAMES (its "Linked To" column) needs **select**;
# * a party whose directory a call CHANGES needs **write** on that party;
# * a Contact or Address whose own record a call CHANGES needs **write** on it;
# * every document name the request carries is **text** (:func:`_docname`), so the
#   name that was checked is the name that is queried.
#
# "Party" is whatever document the widget names as a source: the open form itself,
# its Customer or Supplier, an Opportunity's Lead, each stakeholder row.
# ---------------------------------------------------------------------------

#: What the directory lists, links and unlinks. The widget never sends anything else.
DIRECTORY_DOCTYPES = ("Contact", "Address")


def _docname(value):
    """A document name from the request as text, or None when it is not a name at all.

    The same value has to govern the permission check and the query that follows it, and
    a non-string lets them part. Frappe's whitelist does not check the type of an
    unannotated argument, and a JSON body keeps an integer an integer and an object a
    dict. ``frappe.has_permission`` on the integer ``5`` loads ONE document (the first
    whose name MariaDB compares equal to 5), while ``link_name IN (5)`` compares the
    VARCHAR column numerically and matches every party whose name starts with a 5; a dict
    handed to ``frappe.db.set_value`` or ``get_all`` is a filter over every matching row.
    So an integer becomes its string, which is exact in both places (an autoincrement key
    compares a string to its number exactly), and anything else that is not a non-empty
    string is refused: ``True`` included, which is an ``int`` to Python and a 1 to MariaDB.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def _require_docname(value, doctype):
    """:func:`_docname`, refusing the call when there is no name."""
    name = _docname(value)
    if name is None:
        frappe.throw(frappe._("Name one {0}.").format(doctype))
    return name


def _assert_directory_doctype(doctype):
    """Refuse anything but a Contact or an Address.

    ``link_existing_record`` and ``unlink_record`` take the doctype from the request.
    """
    if doctype not in DIRECTORY_DOCTYPES:
        frappe.throw(
            frappe._("The directory links and unlinks Contacts and Addresses, not {0}.").format(doctype)
        )


def _permitted(doctype, name, ptype):
    """Whether the session user holds ``ptype`` on the document ``doctype`` / ``name``, quietly.

    Never raises and never queues a message: a DocType that does not exist, a child
    table, and a document that does not exist all answer False. The last one is ordinary,
    not hostile: an unsaved form sends its placeholder name (``new-project-…``) as its own
    source. ``frappe.has_permission`` on a docname that does not exist raises
    ``DoesNotExistError`` through ``frappe.throw``, whose message the Desk still shows when
    the exception is caught, hence the existence checks before it.
    """
    name = _docname(name)
    if not (isinstance(doctype, str) and doctype and name):
        return False
    if not frappe.db.exists("DocType", doctype, cache=True) or frappe.is_table(doctype):
        return False
    if not frappe.db.exists(doctype, name):
        return False
    try:
        return bool(frappe.has_permission(doctype, ptype, doc=name))
    except frappe.DoesNotExistError:
        # frappe.db.exists answers a pair whose name equals its doctype without looking.
        return False


def _readable_parties(sources):
    """The parties in ``sources`` the session user may read, as ``{doctype: [names]}``.

    ``sources`` is the widget's ``[{doctype, name}, ...]`` list (a JSON string over HTTP).

    A party the user may not read is **dropped, not refused**. The widget sends every
    party the open form draws its directory from (``get_all_party_sources`` in
    ``unified_tab_controller.js``): the form itself, its Customer or Supplier, an
    Opportunity's Lead or Prospect, each stakeholder row. Someone who may read a Project
    can well be unable to read one of those; stock ERPNext gives Projects User only
    *select* on Customer. Raising would replace the whole directory with an error on every
    refresh of a form the user is entitled to open. Dropping shows the contacts of every
    party they may read, and a caller who may read none of them gets an empty list.

    Each name is kept as :func:`_docname` gives it, the string that was checked, because
    it goes straight into :func:`_linked_records`' ``IN`` filter.
    """
    import json

    if isinstance(sources, str):
        sources = json.loads(sources)

    readable = {}
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        doctype, name = source.get("doctype"), _docname(source.get("name"))
        if not isinstance(doctype, str) or not name or name in readable.get(doctype, ()):
            continue
        if _permitted(doctype, name, "read"):
            readable.setdefault(doctype, []).append(name)
    return readable


def _linked_records(parenttype, parties):
    """Names of the Contacts or Addresses (``parenttype``) Dynamic-Linked to any of ``parties``.

    Keyed on the (doctype, name) **pair**. The query this replaced matched ``link_name``
    alone, so a Customer and a Supplier that share a name shared a directory, and once
    reading a party became the gate, reading one would have returned the other's contacts.
    """
    names = []
    for doctype, party_names in parties.items():
        names += frappe.get_all(
            "Dynamic Link",
            filters={
                "parenttype": parenttype,
                "link_doctype": doctype,
                "link_name": ["in", party_names],
            },
            pluck="parent",
        )
    return list(dict.fromkeys(names))


def _visible_links(parenttype, record_names, parties):
    """Each record's Dynamic Links as ``{record: [{name, doctype}, ...]}``, less the parties
    the caller may not see.

    The directory's "Linked To" column names every party a Contact or Address belongs to.
    Until the v1.561.1 review it named them all, so read on one Customer also showed the
    names of every other party its contacts were linked to: other customers, Leads,
    Projects. A link is now kept when the caller holds **select** on its party, the
    permission Frappe uses for "may see this name in a Link field" (``has_permission``
    treats read as implying it). Not read: stock ERPNext gives Projects User only select
    on Customer, and the Project's own Customer field already shows them that name, so
    read would take the Customer off every row of their directory and hide nothing.

    ``parties`` passed the read check already. Any other party is checked once per call,
    quietly (:func:`_permitted`), so a link to a party that no longer exists drops too.
    """
    rows = frappe.get_all(
        "Dynamic Link",
        filters={"parent": ["in", record_names], "parenttype": parenttype},
        fields=["parent", "link_doctype", "link_name"],
    )
    visible = {(doctype, name): True for doctype, names in parties.items() for name in names}
    link_map = {}
    for row in rows:
        pair = (row.link_doctype, row.link_name)
        if pair not in visible:
            visible[pair] = _permitted(row.link_doctype, row.link_name, "select")
        if visible[pair]:
            link_map.setdefault(row.parent, []).append({"name": row.link_name, "doctype": row.link_doctype})
    return link_map


def _requested_links(link_doctype, link_name, links):
    """The ``(doctype, name)`` pairs a link request names, de-duplicated, in order.

    ``links`` is the list form (a JSON string over HTTP) that the directory widget sent
    until v1.575.0; ``link_doctype`` / ``link_name`` is the single-link form it sends now.
    An entry that is not a usable pair is kept as ``None`` so the caller refuses the
    request rather than quietly linking whatever else it carried.
    """
    import json

    if links:
        entries = json.loads(links) if isinstance(links, str) else links
        if not isinstance(entries, (list, tuple)):
            entries = [entries]
    elif link_doctype or link_name:
        entries = [{"link_doctype": link_doctype, "link_name": link_name}]
    else:
        entries = []

    pairs = []
    for entry in entries:
        pair = None
        if isinstance(entry, dict):
            dt, nm = entry.get("link_doctype"), _docname(entry.get("link_name"))
            if isinstance(dt, str) and dt and nm:
                pair = (dt, nm)
        if pair not in pairs:
            pairs.append(pair)
    return pairs


@frappe.whitelist()
def link_existing_record(doctype, docname, link_doctype=None, link_name=None, links=None):
    """Link an existing Contact or Address to ONE document: the one the user is viewing.

    **One link, never more** (v1.575.0). Until then the directory widget sent every party
    the open form draws its directory from (the form, its Customer or party, every Project
    Stakeholder row) and this appended a link to each one the user could write. Linking a
    county health inspector from a Harwood project therefore filed him as a contact of
    Harwood *and* of Layton Construction, the project's general contractor, and
    ``contacts_ux`` then named Harwood as his Account. The 2026-10-06 audit found 15
    people and 2 addresses attached to companies they have nothing to do with this way
    (and a 16th, the company's CPA on its own Customer, removed on the user's decision),
    and everyone who uses the directory holds write on both Customer and Supplier, so the
    write check below never narrowed it. "This person is on this job" is a link to the
    job; whether they work for a company is decided on that company's form or by the
    Contact's own Account field, not as a side effect of a project.

    So a request naming more than one record is **refused**, not trimmed: a Desk tab still
    running the pre-fix script sends the old list, and quietly keeping its first entry
    would hide that the user is on a stale page. Nothing is written in that case.

    **Permissions** (v1.561.1): write on the Contact/Address, because adding a link row
    changes that record; and write on the document it is linked to, because the link puts
    the record in that document's directory. Both are checked before anything is written.
    A document that does not exist (an unsaved form's placeholder name) raises.

    Every name is taken as text (:func:`_docname`): an integer ``link_name`` would pass the
    write check on one party and then clear the exclusions of every party whose name
    starts with the same digits.
    """
    _assert_directory_doctype(doctype)
    docname = _require_docname(docname, doctype)
    frappe.has_permission(doctype, "write", doc=docname, throw=True)

    pairs = _requested_links(link_doctype, link_name, links)
    if len(pairs) > 1:
        frappe.throw(
            frappe._(
                "Link Existing links a {0} to the record you are viewing and nothing else. "
                "This page is running an older version: reload it (Ctrl+Shift+R) and try again."
            ).format(frappe._(doctype)),
            title=frappe._("Reload the page"),
        )
    if not pairs or pairs[0] is None:
        frappe.throw(frappe._("Name the one record to link this {0} to.").format(frappe._(doctype)))

    link_dt, link_nm = pairs[0]
    if frappe.is_table(link_dt) or not frappe.has_permission(link_dt, "write", doc=link_nm):
        frappe.throw(
            frappe._("You do not have permission to change the directory of {0} {1}.").format(
                frappe._(link_dt), link_nm
            ),
            exc=frappe.PermissionError,
        )

    doc = frappe.get_doc(doctype, docname)
    if not any(l.link_doctype == link_dt and l.link_name == link_nm for l in doc.links):
        doc.append("links", {"link_doctype": link_dt, "link_name": link_nm})
        doc.save()

    # Re-linking clears any prior "hidden from this directory" exclusion so the record
    # shows up again where it was added.
    _remove_exclusion(link_dt, link_nm, doctype, docname)
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

    **Permissions** (v1.561.1; there were none before): write on the document being
    viewed, always, since the exclusion changes its directory either way; and write on
    the Contact/Address when a direct link row is removed from it, since that changes the
    record. A Contact merely inherited from the Customer is hidden without being touched,
    so it needs no write on the Contact. Both are checked before anything is written, and
    the save no longer passes ``ignore_permissions``. Both names are taken as text
    (:func:`_docname`).
    """
    _assert_directory_doctype(doctype)
    docname = _require_docname(docname, doctype)
    link_name = _require_docname(link_name, link_doctype)
    frappe.has_permission(link_doctype, "write", doc=link_name, throw=True)
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
        frappe.has_permission(doctype, "write", doc=docname, throw=True)
        doc.set("links", new_links)
        doc.save()

    # Hide it from this document's directory regardless of how it was surfaced,
    # while preserving every remaining link (Customer, Supplier, etc.).
    _add_exclusion(link_doctype, link_name, doctype, docname)

    return True

@frappe.whitelist()
def get_contacts_for_context(sources, context_doctype=None, context_name=None):
    """Aggregate the de-duplicated Contact list for a document's directory.

    ``sources`` is a list of ``{doctype, name}`` records whose linked Contacts
    should be pooled (e.g. the document itself plus its related party).
    ``context_doctype`` / ``context_name`` identify the document being viewed so
    its per-document exclusions can be applied. Each returned Contact is
    annotated with its full set of Dynamic Links.

    **Permissions** (v1.561.1; there were none before): only the contacts of sources the
    user may **read** are returned, and the rest are dropped without a word (see
    :func:`_readable_parties` for why dropping, not refusing). Read on the party is the
    whole gate, as it was for the Desk: the directory is a view of a party's people for
    whoever may open that party, not a Contact list. Each row's ``links`` name only the
    parties the user may select (:func:`_visible_links`).
    """
    parties = _readable_parties(sources)
    if not parties:
        return []

    linked = _linked_records("Contact", parties)
    if not linked:
        return []

    contacts = frappe.get_all(
        "Contact",
        filters={"name": ["in", linked]},
        fields=["name", "first_name", "last_name", "custom_title", "custom_phone_number", "custom_mobile_number", "custom_email", "is_primary_contact"]
    )

    unique_contacts = {c.name: c for c in contacts}

    # Drop contacts the user has unlinked from this specific document's directory.
    excluded = _get_excluded_names(context_doctype, _docname(context_name), "Contact")
    contact_list = [c for c in unique_contacts.values() if c.name not in excluded]

    if not contact_list:
        return []

    link_map = _visible_links("Contact", [c.name for c in contact_list], parties)

    for c in contact_list:
        c.links = link_map.get(c.name, [])

    return contact_list

@frappe.whitelist()
def get_importable_contacts(target_doctype, target_name, sources=None):
    """The related parties' Contacts that this document does not carry yet.

    Backs the directory widget's **Import Contacts** dialog. Until now the only
    way to put a Customer's people onto a Project or an Opportunity was Link
    Existing, one Contact at a time, typed by name into a Link field — so the
    common case (a new job for an account with five contacts) was five prompts
    and a memory test.

    ``sources`` is the same ``[{doctype, name}, ...]`` list
    :func:`get_contacts_for_context` takes. The client already computes it
    (``unified_tab_controller.get_all_party_sources``, which knows that an
    Opportunity's party hangs off ``opportunity_from`` rather than
    ``party_type``); re-deriving it here would be a second answer to "which
    parties does this document inherit contacts from", free to drift from the
    one the directory below the dialog is rendered from.

    **This returns what is missing, not the directory.** A Contact already
    linked straight to this document is dropped: offering it would make ticking
    it a silent no-op and turn the "3 contacts linked" confirmation into a lie.
    Contacts the user previously *unlinked* here are dropped too — they carry a
    ``Directory Link Exclusion`` row and :func:`get_contacts_for_context`
    already applies it — so a bulk import can never quietly undo a deliberate
    unlink. Link Existing remains the way back for one that was hidden on
    purpose, exactly as it is today.

    Read on the target is refused loudly; a related party the user may not read
    contributes nothing, because :func:`get_contacts_for_context` drops it (v1.561.1).
    """
    target_name = _require_docname(target_name, target_doctype)
    frappe.has_permission(target_doctype, "read", doc=target_name, throw=True)

    contacts = get_contacts_for_context(sources or [], target_doctype, target_name)
    if not contacts:
        return []

    already_linked = set(
        frappe.get_all(
            "Dynamic Link",
            filters={
                "link_doctype": target_doctype,
                "link_name": target_name,
                "parenttype": "Contact",
            },
            pluck="parent",
        )
    )
    return [c for c in contacts if c.get("name") not in already_linked]


@frappe.whitelist()
def import_contacts(target_doctype, target_name, contacts):
    """Link exactly the chosen Contacts to one document, and no others.

    The write half of the Import Contacts dialog. ``contacts`` is the list of
    Contact names the user ticked (JSON string or list); every Contact outside
    that list is left alone, including the ones the dialog offered and the user
    did not choose. Ticking two of five links two.

    Returns ``{"linked": n, "skipped": m, "contacts": [...]}`` — the caller
    reports the number, so it has to be the number of links actually created,
    not the number requested. A name that no longer exists, or that is already
    linked, counts as skipped rather than raising: the dialog is built from a
    list that was accurate when it was opened, and a Contact deleted or linked
    in another tab in the meantime is not an error worth throwing an import
    away over.

    **The permission gates are write on the target document and write on each
    Contact that gains the link** — the same two :func:`link_existing_record`
    applies (v1.561.1). Until then this checked the target only and saved the
    Contacts with ``ignore_permissions``, and ``contacts`` is whatever names the
    request carries, not only the ones the dialog offered, so write on the target
    stood in for permission on every Contact named. Every Contact is checked
    before any is saved, so a refusal links nothing. A Contact that is already
    linked here is not changed, so it needs no write. ``target_name`` is taken as text
    (:func:`_docname`), since it is written into each link row and matched against the
    exclusions this clears.
    """
    import json

    if isinstance(contacts, str):
        contacts = json.loads(contacts)
    if not isinstance(contacts, (list, tuple)):
        frappe.throw(frappe._("Pass the list of Contacts to import."))

    names = [str(c).strip() for c in contacts if str(c or "").strip()]
    if not names:
        return {"linked": 0, "skipped": 0, "contacts": []}

    target_name = _require_docname(target_name, target_doctype)
    frappe.has_permission(target_doctype, "write", doc=target_name, throw=True)

    linked = []
    skipped = []
    present = []
    to_link = []

    # dict.fromkeys de-duplicates while preserving the order the user sees.
    for name in dict.fromkeys(names):
        if not frappe.db.exists("Contact", name):
            skipped.append(name)
            continue

        present.append(name)
        contact = frappe.get_doc("Contact", name)
        if any(
            l.link_doctype == target_doctype and l.link_name == target_name
            for l in contact.links
        ):
            skipped.append(name)
        else:
            to_link.append(contact)

    for contact in to_link:
        frappe.has_permission("Contact", "write", doc=contact.name, throw=True)

    for contact in to_link:
        contact.append(
            "links", {"link_doctype": target_doctype, "link_name": target_name}
        )
        contact.save()
        linked.append(contact.name)

    # Picking a Contact here is deliberate, so it also clears any prior
    # "hidden from this directory" row — same as link_existing_record.
    for name in present:
        _remove_exclusion(target_doctype, target_name, "Contact", name)

    return {"linked": len(linked), "skipped": len(skipped), "contacts": linked}


@frappe.whitelist()
def get_addresses_for_context(sources, context_doctype=None, context_name=None):
    """Aggregate the de-duplicated Address list for a document's directory.

    Address counterpart of :func:`get_contacts_for_context`, with the same gate: only
    the addresses of sources the user may read, each naming only the parties the user
    may select (v1.561.1).
    """
    parties = _readable_parties(sources)
    if not parties:
        return []

    linked = _linked_records("Address", parties)
    if not linked:
        return []

    addresses = frappe.get_all(
        "Address",
        filters={"name": ["in", linked]},
        fields=["name", "address_title", "address_type", "address_line1", "address_line2", "city", "state", "pincode", "country", "is_primary_address", "custom_full_address"]
    )

    unique_addresses = {a.name: a for a in addresses}

    # Drop addresses the user has unlinked from this specific document's directory.
    excluded = _get_excluded_names(context_doctype, _docname(context_name), "Address")
    address_list = [a for a in unique_addresses.values() if a.name not in excluded]

    if not address_list:
        return []

    link_map = _visible_links("Address", [a.name for a in address_list], parties)

    for a in address_list:
        a.links = link_map.get(a.name, [])

    return address_list


def apply_primary_contact_details(doc, contact=None):
    """Mirror a Contact's details onto a party's ``primary_contact_*`` fields.

    The programmatic counterpart of what ``primary_contact.js`` does when a user
    picks a contact on the form: any code that *sets* ``primary_contact`` without
    a browser in the loop (the fountain-move intake, the Opportunity -> Project
    hand-off) has to fill these three itself. Only non-empty values are written.

    Leaving them blank is not the same as not calling this. The party's
    ``on_update`` runs :func:`sync_from_main_doc`, which pushes the same three
    fields back *down* onto the Contact — and its job-title branch has no
    truthiness guard, so a blank ``primary_contact_job_title`` erases the
    Contact's ``custom_title`` on the first save.

    Guarded with ``has_field`` because these three fields exist only in the live
    database (they are in neither the fixtures nor ``setup/custom_fields.py``), so
    a fresh install genuinely does not have them.
    """
    contact = contact or doc.get("primary_contact")
    if not contact:
        return

    details = frappe.db.get_value(
        "Contact",
        contact,
        ["custom_email", "custom_phone_number", "custom_mobile_number", "custom_title"],
        as_dict=True,
    ) or {}
    meta = frappe.get_meta(doc.doctype)
    mapping = {
        "primary_contact_email": details.get("custom_email"),
        "primary_contact_phone": details.get("custom_phone_number") or details.get("custom_mobile_number"),
        "primary_contact_job_title": details.get("custom_title"),
    }
    for field, value in mapping.items():
        if value and meta.has_field(field):
            doc.set(field, value)


def sync_from_main_doc(doc, method):
    """Push a party's primary-contact convenience fields down onto the Contact.

    Wired to ``doc_events`` ``on_update`` of Project / Opportunity / Supplier /
    Customer (see hooks.py). Copies the party's ``primary_contact_phone`` /
    ``_email`` / ``_job_title`` onto the linked Contact's ``custom_*`` fields,
    setting ``flags.is_syncing`` so the reverse hook (:func:`sync_from_contact`)
    does not bounce the change back.
    """
    if not getattr(doc, "primary_contact", None):
        return

    # Skip on existing docs where the primary_contact link itself just changed:
    # in that case the party's convenience fields have not yet been re-fetched
    # for the new contact, so syncing them down would clobber the Contact with
    # stale values. New docs (and edits that keep the same contact) sync through.
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
    """Push a Contact's ``custom_*`` fields up onto every party it leads.

    Wired to Contact ``on_update`` (see hooks.py). Finds every Project /
    Opportunity / Supplier / Customer whose ``primary_contact`` is this Contact
    and copies the title / phone / email onto their convenience fields. Skips
    when ``flags.is_syncing`` is set (the change originated from
    :func:`sync_from_main_doc`), preventing an infinite save loop.
    """
    if getattr(doc.flags, "is_syncing", False):
        return

    custom_title = getattr(doc, "custom_title", None) or ""
    custom_phone = getattr(doc, "custom_phone_number", None) or ""
    custom_mobile = getattr(doc, "custom_mobile_number", None) or ""
    custom_email = getattr(doc, "custom_email", None) or ""

    phone_to_sync = custom_phone or custom_mobile

    for dt in PRIMARY_CONTACT_DOCTYPES:
        # `primary_contact` is a custom field; skip doctypes where it isn't
        # installed (e.g. a fresh test DB) to avoid "Unknown column" errors.
        if not frappe.db.has_column(dt, "primary_contact"):
            continue
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
