"""Submittal packet assembly for a Water Feature Design, and the print formats' Jinja helpers.

Assembles the health-department plan set (cover and title block, design basis, schedules,
electrical, structural, drawings, design calculations, the calculation audit, standard details
and notes) into one branded HTML print format ("Water Feature Design - Submittal Packet").
Since phase G of the Fountain Designer (v1.770.0) every sheet reads the schema-2 record through
``we_print``: the stored ``model_json``, ``results_json`` and ``bom`` rows, built into sheets by
the frappe-free ``prints.build``. Nothing on paper recomputes the engine.

The packet is a DRAFT for a licensed P.E. to review and seal: it carries a
"PRELIMINARY — NOT FOR CONSTRUCTION" watermark and a stamp placeholder until a PE
signs (`status = Issued` means the package is complete, not sealed). HTML-first:
`build_packet_html` renders the packet HTML (usable via browser print-to-PDF
today); the server-side combined PDF + Drive upload is deferred until the prod
PDF backend is fixed (docs/pdf-generation.md).

The Jinja helpers here are registered in hooks.py `jinja.methods` and compute in
Python what the print sandbox can't: the print context (``we_print``), the resolved
title block, the stored drawings, and the code-keyed standard notes and details.
"""

import json

import frappe
from frappe import _

from erpnext_enhancements.utils.normalize import phone_display
from erpnext_enhancements.water_engineering import prints

PACKET_PF = prints.PF_PACKET
DESIGN_DOCTYPE = "Water Feature Design"

# The fountain venues (mirrors engine.aquatic) — a regulated packet is any other.
_FOUNTAIN_VENUES = {"", "decorative fountain", "interactive water feature"}


def _doc(design):
    return frappe.get_doc(DESIGN_DOCTYPE, design) if isinstance(design, str) else design


def _settings():
    """Water Engineering Settings singleton (engineer identity / PE stamp)."""
    try:
        return frappe.get_cached_doc("Water Engineering Settings")
    except Exception:
        return frappe._dict()


def is_regulated_packet(doc):
    return (doc.get("venue_type") or "").strip().lower() not in _FOUNTAIN_VENUES


def we_title_block(design):
    """Resolved title-block data (engineer / contractor / project / dates /
    revisions) for the packet cover + each sheet header."""
    doc = _doc(design)
    s = _settings()
    project = {}
    if doc.get("project"):
        project = frappe.db.get_value("Project", doc.project, ["project_name"], as_dict=True) or {}
    return {
        "engineer": {
            "name": s.get("engineer_name"),
            "license": s.get("engineer_license"),
            "address": s.get("engineer_address"),
            "phone": phone_display(s.get("engineer_phone")),
            "stamp": s.get("engineer_stamp"),
        },
        "contractor": {
            "name": s.get("contractor_name") or "Sapphire Fountains",
            "address": s.get("contractor_address"),
            "phone": phone_display(s.get("contractor_phone")),
        },
        "project": {
            "name": project.get("project_name") or doc.get("design_title") or doc.name,
            "customer": doc.get("customer"),
        },
        "issue_date": doc.get("issue_date"),
        "drawn_by": doc.get("drawn_by"),
        "governing_code": doc.get("governing_code"),
        "venue_type": doc.get("venue_type"),
        "revisions": [
            {"mark": r.mark, "date": r.rev_date, "description": r.description}
            for r in doc.get("revisions") or []
        ],
        # PE stamp is placed only when a stamp image exists AND the design is
        # Issued; otherwise the packet is a preliminary draft.
        "preliminary": not (s.get("engineer_stamp") and doc.get("status") == "Issued"),
    }


def _json(raw, kind=dict):
    """A JSON field as ``kind``: it reads back as text or already parsed, and may be junk."""
    if isinstance(raw, kind):
        return raw
    if not raw:
        return kind()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return kind()
    return data if isinstance(data, kind) else kind()


RECORD_FIELDS = ("name", "design_title", "project", "customer", "opportunity", "status", "venue_type", "governing_code",
                 "issue_date", "drawn_by", "docstatus")
BOM_FIELDS = ("section", "item", "description", "qty", "uom", "rate", "amount", "source", "catalog_source", "line_key")


def _item_info(codes):
    """``{item: {item_name, model_no, spec_url}}`` for the Items a print names. The model number and
    the cut-sheet link are the catalog's custom fields (phase D), read only where Item has them."""
    codes = sorted({c for c in codes if c})
    if not codes:
        return {}
    try:
        meta = frappe.get_meta("Item")
        fields = ["name", "item_name"] + [f for f in ("custom_model_no", "custom_spec_source_url") if meta.has_field(f)]
        rows = frappe.get_all("Item", filters={"name": ["in", codes]}, fields=fields)
    except Exception:
        return {}
    return {
        r["name"]: {"item_name": r.get("item_name"), "model_no": r.get("custom_model_no"),
                    "spec_url": r.get("custom_spec_source_url")}
        for r in rows
    }


def print_inputs(doc):
    """The record as plain data for ``prints.build``: fields, model, stored derive, BOM rows, issues."""
    model = _json(doc.get("model_json"))
    stored = _json(doc.get("results_json"))
    bom = [{f: getattr(r, f, None) if not isinstance(r, dict) else r.get(f) for f in BOM_FIELDS} for r in doc.get("bom") or []]
    codes = [r.get("item") for r in bom]
    net = model.get("network") or {}
    codes += [p.get("item") for p in net.get("pumps") or [] if isinstance(p, dict)]
    codes += [e.get("item") for e in model.get("equipment") or [] if isinstance(e, dict)]
    codes += [(f.get("product") or {}).get("item") for f in model.get("features") or [] if isinstance(f, dict)]
    return {
        "doc": {f: doc.get(f) for f in RECORD_FIELDS},
        "model": model,
        "stored": stored,
        "bom": bom,
        "items": _item_info(codes),
        "issues": _json(doc.get("design_issues_json"), list),
    }


def we_print(design, sheets=None):
    """The print context of a design (``prints.build``): every Water Feature Design format and the
    packet call this once with the sheets they print. Never raises: a record it cannot read prints
    the "not worked out yet" sheets rather than a 500 from the print view."""
    try:
        doc = _doc(design)
        data = print_inputs(doc)
        return prints.build(data["doc"], data["model"], data["stored"], data["bom"], items=data["items"],
                            issues=data["issues"], sheets=sheets)
    except Exception:
        frappe.log_error(title="Water Feature Design print", message=frappe.get_traceback())
        name = design if isinstance(design, str) else getattr(design, "name", "")
        return prints.build({"name": name}, {}, {}, [], sheets=sheets)


def we_circulation_schematic(design):
    """The stored circulation schematic (SVG) of the last derive, or ``""``."""
    doc = _doc(design)
    return prints.unique_ids((_json(doc.get("results_json")).get("charts") or {}).get("schematic_svg"), "sch") or ""


def we_electrical_oneline(design):
    """The stored electrical one-line (SVG) of the last derive, from phase L's report, or ``""``."""
    doc = _doc(design)
    return prints.unique_ids((_json(doc.get("results_json")).get("charts") or {}).get("oneline_svg"), "one") or ""


def we_standard_notes(governing_code=None, category=None):
    """Active Standard Notes, filtered to the governing code (blank = any) and
    optionally a category, ordered for the SP-5 notes sheet."""
    filters = {"active": 1}
    if category:
        filters["category"] = category
    try:
        notes = frappe.get_all(
            "Standard Note",
            filters=filters,
            fields=["note_key", "category", "governing_code", "title", "body", "sort_order"],
            order_by="category asc, sort_order asc, note_key asc",
        )
    except Exception:
        return []
    if governing_code:
        notes = [n for n in notes if not n.get("governing_code") or n["governing_code"] == governing_code]
    return notes


def we_standard_details(governing_code=None, category=None):
    """Active Standard Details (inline SVG), filtered to the governing code
    (blank = any) and optionally a category, for the SP-4 details sheet."""
    filters = {"active": 1}
    if category:
        filters["category"] = category
    try:
        details = frappe.get_all(
            "Standard Detail",
            filters=filters,
            fields=["detail_key", "category", "governing_code", "title", "svg", "sort_order"],
            order_by="category asc, sort_order asc, detail_key asc",
        )
    except Exception:
        return []
    if governing_code:
        details = [d for d in details if not d.get("governing_code") or d["governing_code"] == governing_code]
    return details


def build_packet_html(design):
    """Render the assembled submittal packet HTML for a design."""
    doc = _doc(design)
    return frappe.get_print(DESIGN_DOCTYPE, doc.name, print_format=PACKET_PF)


@frappe.whitelist()
def assemble_packet(design):
    """Whitelisted: return the assembled packet HTML (for preview / download).
    Read-gated on the design. The HTML is the source of truth and is browser
    print-to-PDF ready; ``generate_packet`` additionally produces the PDF."""
    if not frappe.has_permission(DESIGN_DOCTYPE, "read"):
        frappe.throw(_("You do not have access to Water Feature Designs."), frappe.PermissionError)
    return build_packet_html(design)


# ---------------------------------------------------------------- PDF + Drive


def _cad_pdf_files(doc):
    """PDF File attachments on the design — candidate plan-specific CAD drawings
    to append after the app-generated sheets — excluding the generated packet."""
    packet_url = doc.get("packet_document")
    try:
        rows = frappe.get_all(
            "File",
            filters={"attached_to_doctype": DESIGN_DOCTYPE, "attached_to_name": doc.name},
            fields=["name", "file_url", "file_name"],
            order_by="creation asc",
        )
    except Exception:
        return []
    out = []
    for r in rows:
        url = (r.get("file_url") or "").lower()
        if not url.endswith(".pdf"):
            continue
        if packet_url and r.get("file_url") == packet_url:
            continue
        if "submittal-packet" in (r.get("file_name") or "").lower():
            continue
        out.append(r)
    return out


def _file_bytes(file_row):
    try:
        return frappe.get_doc("File", file_row["name"]).get_content()
    except Exception:
        return None


def _merge_cad_pdfs(doc, app_pdf):
    """Append the design's attached CAD PDFs after the app sheets with pypdf.
    Best-effort: returns ``app_pdf`` unchanged if pypdf is missing or a CAD file
    can't be read (DWG/other non-PDF CAD is left as separate Drive files)."""
    cad = _cad_pdf_files(doc)
    if not cad:
        return app_pdf
    try:
        import io

        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(app_pdf)))
        for f in cad:
            content = _file_bytes(f)
            if content:
                try:
                    writer.append(PdfReader(io.BytesIO(content)))
                except Exception:
                    continue
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water packet CAD merge")
        return app_pdf


def _attach_packet_pdf(doc):
    """Best-effort: render the packet PDF via the chrome generator, append the
    CAD drawings, and attach it as a private File to the Project (so drive_sync
    mirrors it to the project Drive folder) — or to the design if there is no
    project. Returns the File URL, or None on failure. Never raises."""
    try:
        pdf = frappe.get_print(
            DESIGN_DOCTYPE, doc.name, print_format=PACKET_PF, as_pdf=True, pdf_generator="chrome"
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water packet PDF (chrome)")
        return None
    if not pdf:
        return None
    pdf = _merge_cad_pdfs(doc, pdf)
    safe = frappe.utils.cstr(doc.name).replace("/", "-")
    if doc.get("project"):
        attached_to_doctype, attached_to_name = "Project", doc.project
    else:
        attached_to_doctype, attached_to_name = DESIGN_DOCTYPE, doc.name
    try:
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"{safe}-submittal-packet.pdf",
                "content": pdf,
                "is_private": 1,
                "attached_to_doctype": attached_to_doctype,
                "attached_to_name": attached_to_name,
            }
        ).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water packet File attach")
        return None
    doc.db_set("packet_document", file_doc.file_url, update_modified=False)
    return file_doc.file_url


@frappe.whitelist()
def generate_packet(design):
    """Whitelisted (write-gated): snapshot the packet HTML onto the design and
    produce the PDF (chrome) with the CAD drawings merged in, attached to the
    Project so it syncs to Drive. HTML is the record of truth; the PDF is
    best-effort and never blocks. Returns the stored HTML flag + PDF URL."""
    if not frappe.has_permission(DESIGN_DOCTYPE, "write"):
        frappe.throw(_("You do not have write permission for Water Feature Designs."), frappe.PermissionError)
    doc = _doc(design)
    html = None
    try:
        html = build_packet_html(doc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water packet HTML render")
    if html:
        doc.db_set("packet_html", html, update_modified=False)
    url = _attach_packet_pdf(doc)
    frappe.db.commit()
    return {"packet_html_stored": bool(html), "packet_document": url}
