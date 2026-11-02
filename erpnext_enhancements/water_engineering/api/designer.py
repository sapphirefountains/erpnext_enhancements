"""Whitelisted endpoints for the Fountain Designer page (``page/fountain_designer``).

The contract is ``public/js/fountain_designer/README.md``, "The contract", field for field;
``docs/fountain-designer/PLAN.md`` section 3.4 is the plan behind it. Every endpoint is a thin
adapter: the ladder is ``water_engineering.derive.derive_model`` (pure, bench-free) and this
module only hands it what lives in the database — the house rules on Water Engineering Settings,
the pump catalog from phase D's ``catalog_reader``, Item prices, and the geocoded address.

``boot``, ``get_design`` and ``catalog_search`` are called as GET, so none of them is
``methods=["POST"]``. Permissions gate on the ``Water Feature Design`` doctype like
``api/water_design.py`` (read to look, write to save); ``doc.save()`` enforces the document-level
permission on every mutation. ``api/water_design.py`` stays for the old wizard until phase H.
"""

import copy
import hashlib
import json

import frappe
from frappe import _

from erpnext_enhancements.water_engineering import derive as derive_mod
from erpnext_enhancements.water_engineering import prints as prints_mod
from erpnext_enhancements.water_engineering.engine import kits as kits_engine
from erpnext_enhancements.water_engineering.engine import model as model_engine
from erpnext_enhancements.water_engineering.engine import rules as rules_engine
from erpnext_enhancements.water_engineering.engine.families import FAMILIES

DESIGN_DOCTYPE = "Water Feature Design"
SETTINGS_DOCTYPE = "Water Engineering Settings"
DESIGNER_ROUTE = "fountain-designer"

# The record's own fields a caller may set through save_design (the model carries the rest).
DESIGN_FIELDS = ("design_title", "project", "opportunity", "customer")

# The keys of a derive reply the page reads (README, "derive"). derive_model returns more
# (the electrical, structural and controls detail phase G prints); save keeps all of it.
REPLY_KEYS = ("model", "results", "values", "rollups", "issues", "readiness", "verdict", "bom", "charts", "outputs")

CATALOG_CACHE_KEY = "fountain_designer:pump_catalog"
CATALOG_CACHE_S = 600
GEOCODE_CACHE_S = 30 * 24 * 3600
GEOCODE_RETRY_S = 600

# Family order on the page: the most common first ("Add a feature" falls back to the first).
FAMILY_ORDER = ("weir", "jets", "interactive", "cascade", "laminar", "foam", "pondless", "curtain", "mist", "aerator")
PRODUCT_CLASS = {
    "weir": "weir", "jets": "nozzle", "foam": "nozzle", "laminar": "nozzle", "interactive": "nozzle",
    "cascade": "weir", "pondless": "pump", "curtain": "nozzle", "mist": "nozzle", "aerator": "aerator",
}
# Phase C's input kinds onto the page's (ui.js): a table input has no editor on the page yet.
INPUT_KINDS = {"number": "number", "integer": "int", "select": "select", "bool": "check", "text": "text"}

EQUIPMENT_CLASSES = [
    {"key": "pump", "label": "Pumps", "item_class": "Pump"},
    {"key": "nozzle", "label": "Nozzles", "item_class": "Nozzle"},
    {"key": "weir", "label": "Weirs and sheet falls", "item_class": "Fitting kit"},
    {"key": "filter", "label": "Filters", "item_class": "Filter"},
    {"key": "uv", "label": "UV", "item_class": "Control Hardware"},
    {"key": "light", "label": "Lights", "item_class": "Light"},
    {"key": "aerator", "label": "Aerators", "item_class": "Aerator"},
    {"key": "valve", "label": "Valves", "item_class": "Valve"},
    {"key": "chemical", "label": "Chemical feed", "item_class": "Chemical Feed Pump"},
    {"key": "drive", "label": "Drives", "item_class": "Panel Electrical Device"},
]
SLOT_CLASSES = {
    "filter": "filter", "uv": "uv", "chlorinator": "chemical", "light": "light", "transformer": "light",
    "bypass": "valve", "surge_arrestor": "valve", "autofill": "valve", "prv": "valve", "vfd": "drive",
}

SECTION_LABELS = {
    "features": "Features", "basin": "Basins", "piping": "Piping", "pump": "Pump", "control": "Control",
    "bypass": "Bypass", "surge": "Surge", "treatment": "Treatment", "lighting": "Lighting",
    "electrical": "Electrical", "winterization": "Winterization",
}
SECTION_PRESETS = {
    "piping": [
        {"key": "sch40", "label": "SCH40 PVC", "desc": "Discharge to 6.5 ft/s, suction to 4.5 ft/s (DOC-0049).",
         "values": {"pump_room.material": "SCH40 PVC"}},
        {"key": "sch80", "label": "SCH80 PVC", "desc": "Heavier wall for buried or high-pressure runs.",
         "values": {"pump_room.material": "SCH80 PVC"}},
    ],
    "control": [
        {"key": "across_line", "label": "Across the line", "desc": "The pump runs at full speed.",
         "values": {"network.pumps.0.control": "none"}},
        {"key": "vfd", "label": "Variable speed (VFD)", "desc": "A drive holds the design pressure.",
         "values": {"network.pumps.0.control": "vfd"}},
    ],
    "treatment": [
        {"key": "chlorine_cartridge", "label": "Chlorine and cartridge filter", "desc": "Decorative features.",
         "values": {"treatment.sanitizer": "chlorine", "treatment.uv": False}},
        {"key": "chlorine_uv", "label": "Chlorine, UV and sand filter", "desc": "Interactive features (Utah R392-302).",
         "values": {"treatment.sanitizer": "chlorine", "treatment.uv": True}},
    ],
    "lighting": [
        {"key": "white_switched", "label": "White LED, switched", "desc": "", "values": {"lighting.control": "switched"}},
        {"key": "rgbw_dmx", "label": "RGBW LED on DMX", "desc": "", "values": {"lighting.control": "dmx"}},
    ],
    "electrical": [
        {"key": "240_1", "label": "240 V single phase", "desc": "", "values": {"electrical.service_v": 240, "electrical.phase": 1}},
        {"key": "208_3", "label": "208 V three phase", "desc": "", "values": {"electrical.service_v": 208, "electrical.phase": 3}},
        {"key": "480_3", "label": "480 V three phase", "desc": "", "values": {"electrical.service_v": 480, "electrical.phase": 3}},
    ],
}
OPTIONS = {
    "venue_types": ["Decorative Fountain", "Interactive Water Feature", "Pond / Lake", "Commercial Spa", "Commercial Pool",
                    "Wading Pool", "Therapy Pool", "Event Rental"],
    "jurisdictions": ["UT", "ID", "NV", "AZ", "CO", "WY"],
    "basin_shapes": ["rectangular", "round", "other"],
    "pump_locations": [{"value": "vault", "label": "Vault"}, {"value": "room", "label": "Pump room"},
                       {"value": "submersible", "label": "Submersible"}],
    "materials": ["SCH40 PVC", "SCH80 PVC", "Type K Copper"],
    "controls": [{"value": "none", "label": "None"}, {"value": "vfd", "label": "VFD"}, {"value": "prv", "label": "PRV"}],
    "pump_classes": ["end-suction", "self-priming", "submersible", "vertical turbine"],
    "sanitizers": ["chlorine", "bromine", "ozone", "none"],
    "lighting_controls": ["switched", "timer", "dmx"],
    "service_voltages": [120, 208, 240, 480],
    "lines": ["suction", "discharge"],
}


# ----------------------------------------------------------------- helpers


def _parse(value):
    """A dict from a dict or JSON text (frappe.call sends objects as JSON)."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            frappe.throw(_("The design could not be read: it is not valid JSON."))
    return value if isinstance(value, dict) else {}


def _require(ptype):
    if not frappe.has_permission(DESIGN_DOCTYPE, ptype):
        frappe.throw(
            _("You do not have {0} permission for {1}.").format(_(ptype), _(DESIGN_DOCTYPE)), frappe.PermissionError
        )


def _log(stage, exc):
    frappe.log_error(title=f"Fountain Designer derive: {stage}", message=frappe.get_traceback() or repr(exc))


# ------------------------------------------------------------ the loaders


def load_rules():
    """The house rules: Water Engineering Settings over the engine's defaults. Never raises."""
    keys = {r.key for r in rules_engine.RULES} | {rules_engine.SET_ON_FIELD}
    try:
        settings = frappe.get_cached_doc(SETTINGS_DOCTYPE).as_dict()
    except Exception:
        settings = {}
    return rules_engine.resolve({k: v for k, v in settings.items() if k in keys})


def _pump_items():
    meta = frappe.get_meta("Item")
    fields = ["name", "item_name"]
    for f in ("custom_pump_hp", "custom_equipment_class", "custom_generic_estimate", "custom_pump_class"):
        if meta.has_field(f):
            fields.append(f)
    or_filters = [["item_group", "=", "Pumps"]]
    if meta.has_field("custom_equipment_class"):
        or_filters.append(["custom_equipment_class", "=", "Pump"])
    try:
        return frappe.get_all("Item", filters={"disabled": 0}, or_filters=or_filters, fields=fields, limit_page_length=500)
    except Exception:
        return []


def _catalog_row(item, curve):
    source = str(curve.get("source") or "")
    return {
        "item": item.get("name"),
        "name": f"{item.get('item_name') or item.get('name')}" + (f" ({curve['curve_label']})" if curve.get("curve_label") else ""),
        "curve_name": curve.get("name"),
        "points": curve.get("points") or [],
        "shutoff_head_ft": curve.get("shutoff_head_ft"),
        "max_flow_gpm": curve.get("max_flow_gpm"),
        "motor_eff_pct": None,
        "hp": item.get("custom_pump_hp"),
        "generic_class": item.get("custom_pump_class") or None,
        "catalog_source": "generic estimate" if curve.get("is_generic_estimate") else source.lower(),
    }


def load_catalogs():
    """``{"pumps": [...]}`` in phase B's catalog shape: every pump Item's curves from phase D's
    ``catalog_reader.pump_curve_sets``, best curve first. Cached ten minutes, because the page
    derives on every edit. Never raises: no catalog means the engine says so."""
    cached = frappe.cache().get_value(CATALOG_CACHE_KEY)
    if cached:
        return cached
    from erpnext_enhancements.water_engineering import catalog_reader

    items = {i["name"]: i for i in _pump_items()}
    rows = []
    try:
        curves = catalog_reader.pump_curve_sets(list(items))
    except Exception:
        curves = {}
    for code, sets in curves.items():
        for c in sets or []:
            if len(c.get("points") or []) >= 2 or (c.get("shutoff_head_ft") and c.get("max_flow_gpm")):
                rows.append(_catalog_row(items.get(code) or {"name": code}, c))
    # The pumps' rates are cached with them: the derive may pick any of them, on every edit. The
    # valuation rates stay server-side; load_prices serves them only to a user who may read them.
    codes = {r["item"] for r in rows}
    out = {"pumps": rows, "list_rates": list_rates(codes), "valuation_rates": valuation_rates(codes)}
    frappe.cache().set_value(CATALOG_CACHE_KEY, out, expires_in_sec=CATALOG_CACHE_S)
    return out


def site_lookup(site):
    """The geocoded point for the site's address when its lat/lng are blank. A found point, or
    Google's definite ZERO_RESULTS, is cached a month per address; any other failure (a timeout, a
    rate limit, no key) only ten minutes, so it is asked again soon. The controller calls this on a
    save, never the page's per-keystroke derive, so a half-typed address costs nothing. The app has
    no elevation lookup, so elevation comes from the jurisdiction default."""
    site = site or {}
    address = str(site.get("address") or "").strip()
    if not address or (site.get("lat") not in (None, "") and site.get("lng") not in (None, "")):
        return None
    key = "fountain_designer:geocode:" + hashlib.sha1(address.lower().encode("utf-8")).hexdigest()
    cache = frappe.cache()
    hit = cache.get_value(key)
    if hit is not None:
        return hit or None
    try:
        from erpnext_enhancements.workforce.sites import geocode_text

        found, problem = geocode_text(address)
    except Exception:
        found, problem = None, "error"
    point = {"lat": found.get("lat"), "lng": found.get("lng")} if found else {}
    definite = bool(found) or str(problem or "").upper().startswith("ZERO_RESULTS")
    cache.set_value(key, point, expires_in_sec=GEOCODE_CACHE_S if definite else GEOCODE_RETRY_S)
    return point or None


def load_prices(model, catalogs=None):
    """``{items: {item: {rate}}, hints: {}}`` for the Items a derive can name: every catalog pump
    (their rates come cached with the catalog), the model's pumps and its equipment.

    The rate is the Item's price on the default selling price list: the BOM is a budget for a quote,
    and a selling price is what anyone who can read the design may see. The Item's valuation rate (a
    cost) stands in only where there is no list price, and only for a user who may read Item
    valuation (``can_read_valuation``); for anyone else such a line stays unpriced."""
    codes = set()
    for p in (model.get("network") or {}).get("pumps") or []:
        if p.get("item"):
            codes.add(p["item"])
    for e in model.get("equipment") or []:
        if e.get("item"):
            codes.add(e["item"])
    cat = catalogs or {}
    listed = dict(cat.get("list_rates") or {})
    listed.update(list_rates(codes - set(listed)))
    valued = {}
    if can_read_valuation():
        valued = dict(cat.get("valuation_rates") or {})
        valued.update(valuation_rates(codes - set(valued)))
    rates = {}
    for code in set(listed) | set(valued):
        rate = listed.get(code) or valued.get(code)
        if rate:
            rates[code] = {"rate": rate}
    return {"items": rates, "hints": {}}


def can_read_valuation():
    """True when the user may read Item and the permlevel the Item's ``valuation_rate`` sits at."""
    try:
        if not frappe.has_permission("Item", "read"):
            return False
        meta = frappe.get_meta("Item")
        field = meta.get_field("valuation_rate")
        level = (field.permlevel if field else 0) or 0
        return level == 0 or level in meta.get_permlevel_access("read")
    except Exception:
        return False


def list_rates(codes):
    """``{item: rate}`` on the default selling price list."""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    try:
        price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
        rows = frappe.get_all("Item Price", filters={"item_code": ["in", codes], "price_list": price_list},
                              fields=["item_code", "price_list_rate"])
    except Exception:
        return {}
    return {r["item_code"]: r.get("price_list_rate") for r in rows if r.get("price_list_rate")}


def valuation_rates(codes):
    """``{item: valuation_rate}``. Read without the caller's permissions: never send it to a page
    without ``can_read_valuation``."""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    try:
        rows = frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "valuation_rate"])
    except Exception:
        return {}
    return {r["name"]: r.get("valuation_rate") for r in rows if r.get("valuation_rate")}


def run_derive(model, geocode=False):
    """The full derive with every lookup filled in: what the page and the controller both run.
    ``geocode`` is the controller's (a save): the page's debounced derive never calls Google."""
    model = model if isinstance(model, dict) else _parse(model)
    catalogs = load_catalogs()
    lookup = site_lookup(model.get("site")) if geocode else None
    return derive_mod.derive_model(model, catalogs, load_rules(), site_lookup=lookup,
                                   prices=load_prices(model, catalogs), on_error=_log)


def page_reply(out):
    return {k: out.get(k) for k in REPLY_KEYS}


# ------------------------------------------------------------------- boot


def _input_rows(schema):
    rows, shown = [], 0
    for spec in schema or []:
        kind = INPUT_KINDS.get(spec.get("kind"))
        if not kind or spec.get("name") == "look":
            continue
        row = {"key": spec["name"], "label": spec.get("label") or spec["name"], "kind": kind, "unit": spec.get("unit") or ""}
        if spec.get("options"):
            row["options"] = list(spec["options"])
        if not spec.get("required") and shown >= 4:
            row["advanced"] = True
        else:
            shown += 1
        rows.append(row)
    return rows


def _looks(schema):
    spec = next((s for s in schema or [] if s.get("name") == "look"), None)
    return [{"value": o, "label": str(o).replace("_", " ").capitalize()} for o in (spec or {}).get("options") or []]


def families():
    out = []
    for key in sorted(FAMILIES, key=lambda k: FAMILY_ORDER.index(k) if k in FAMILY_ORDER else len(FAMILY_ORDER)):
        m = FAMILIES[key]
        schema = m.inputs()
        out.append({"key": key, "label": m.LABEL, "product_class": PRODUCT_CLASS.get(key), "types": list(m.TYPES),
                    "inputs": _input_rows(schema), "looks": _looks(schema)})
    return out


def kit_model(kit):
    """A kit's starter model as the page stamps it: the engine kit, quick mode, and a Pump room."""
    m = kits_engine.apply_kit(kit.key)
    location = (kit.pump or {}).get("location") or "vault"
    m.update({"mode": "quick", "kit": kit.key, "presets": {},
              "pump_room": {"location": location, "distance_ft": 25, "elevation_ft": None,
                            "route_template": kit.family, "material": "SCH40 PVC"}})
    return m


def _headline(kit, model):
    feats = model.get("features") or []
    if not feats:
        return []
    f = feats[0]
    schema = {s["name"]: s for s in FAMILIES[f["family"]].inputs()} if f.get("family") in FAMILIES else {}
    rows = []
    for key, value in (f.get("inputs") or {}).items():
        spec = schema.get(key)
        if not spec or spec.get("kind") not in ("number", "integer") or isinstance(value, bool) or len(rows) >= 3:
            continue
        rows.append({"path": f"features.0.inputs.{key}", "label": spec.get("label") or key,
                     "kind": "int" if spec["kind"] == "integer" else "number", "unit": spec.get("unit") or "",
                     "min": 0, "max": None, "step": 1 if spec["kind"] == "integer" else 0.5})
    look = schema.get("look")
    if look and (f.get("effect") or {}).get("look"):
        rows.append({"path": "features.0.effect.look", "label": look.get("label") or "Look", "kind": "tiles",
                     "options": [{"value": o, "label": str(o).capitalize(), "desc": look.get("help") or ""}
                                 for o in look.get("options") or []]})
    return rows


def kits():
    out = []
    for kit in kits_engine.KITS:
        m = kit_model(kit)
        out.append({"key": kit.key, "label": kit.label, "family": kit.family,
                    "venue_type": (m.get("site") or {}).get("venue_type") or "Decorative Fountain",
                    "desc": kit.description, "headline": _headline(kit, m), "model": m})
    return out


def sections():
    return [{"key": s, "label": SECTION_LABELS[s], "presets": copy.deepcopy(SECTION_PRESETS.get(s, []))}
            for s in model_engine.SETUP_SECTIONS]


def house_rules(rules=None):
    rows = rules_engine.house_rule_rows(rules or load_rules())
    return [{"key": r["key"], "label": r["label"], "value": r["value"], "unit": r["unit"], "citation": r["citation"]}
            for r in rows]


def print_formats():
    """The Outputs step's papers whose Print Format exists here (``prints.OUTPUTS`` and the packet), as
    ``{key, label, print_format}``. The page links each to the print view of the saved design; a site
    where ``after_migrate`` could not seed a format shows that paper as not available rather than a 404."""
    rows = [{"key": k, "label": label, "print_format": pf} for k, label, pf in prints_mod.OUTPUTS]
    rows.append({"key": "packet", "label": "Submittal packet", "print_format": prints_mod.PF_PACKET})
    try:
        names = sorted({r["print_format"] for r in rows})
        have = set(frappe.get_all("Print Format", filters={"name": ["in", names], "disabled": 0}, pluck="name"))
    except Exception:
        have = set()
    return [r for r in rows if r["print_format"] in have]


@frappe.whitelist()
def boot():
    """Everything the page needs before the first design: kits, families, sections and presets,
    the select options, the catalog classes, the house rules, the print formats the Outputs step
    links to, and what the caller may do."""
    _require("read")
    return {
        "schema": model_engine.SCHEMA,
        "units": "US",
        "can": {
            "read": True,
            "write": bool(frappe.has_permission(DESIGN_DOCTYPE, "write")),
            "create": bool(frappe.has_permission(DESIGN_DOCTYPE, "create")),
            "push_quotation": bool(frappe.has_permission("Quotation", "create")),
            "push_material_request": bool(frappe.has_permission("Material Request", "create")),
            "email": bool(frappe.has_permission(DESIGN_DOCTYPE, "email")),
        },
        "kits": kits(),
        "families": families(),
        "sections": sections(),
        "options": copy.deepcopy(OPTIONS),
        "catalogs": {"equipment_classes": [{"key": c["key"], "label": c["label"]} for c in EQUIPMENT_CLASSES],
                     "slot_classes": dict(SLOT_CLASSES)},
        "house_rules": house_rules(),
        "prints": print_formats(),
    }


# ------------------------------------------------------------------ derive


@frappe.whitelist(methods=["POST"])
def derive(model_json=None):
    """Stateless: the derived model and everything the page draws for it (README, "derive")."""
    _require("read")
    model = _parse(model_json)
    if model_engine.is_legacy(model):
        frappe.throw(_("This design was made before the Fountain Designer and cannot be derived here. Open it in the form."))
    return page_reply(run_derive(model))


# ------------------------------------------------------------- load / save


def stored_model(doc):
    """``model_json`` parsed (the JSON field reads back as text or a dict), or None."""
    raw = doc.get("model_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@frappe.whitelist()
def get_design(name=None):
    """No name: the designs the caller may read, newest first. A name: that design and its model."""
    _require("read")
    if not name:
        rows = frappe.get_list(
            DESIGN_DOCTYPE,
            fields=["name", "design_title", "kit", "mode", "status", "family", "project", "opportunity", "customer",
                    "blocker_count", "warning_count", "issue_ready", "budget_total", "modified"],
            order_by="modified desc",
            limit_page_length=200,
        )
        return {"designs": rows}
    doc = frappe.get_doc(DESIGN_DOCTYPE, name)
    doc.check_permission("read")
    return {
        "name": doc.name,
        "design_title": doc.get("design_title"),
        "mode": doc.get("mode") or "Quick",
        "status": doc.get("status"),
        "kit": doc.get("kit"),
        "project": doc.get("project"),
        "opportunity": doc.get("opportunity"),
        "customer": doc.get("customer"),
        "docstatus": doc.docstatus,
        "can_write": bool(doc.docstatus == 0 and doc.has_permission("write")),
        "modified": str(doc.modified),
        "model": stored_model(doc),
    }


@frappe.whitelist(methods=["POST"])
def save_design(name=None, model_json=None, fields_json=None):
    """Create (no name) or update a design from the page; the save re-derives server-side and writes
    the rollups, the BOM rows and the JSON fields (the controller's validate)."""
    model = _parse(model_json)
    if not model:
        frappe.throw(_("There is no design to save."))
    if model_engine.is_legacy(model):
        frappe.throw(_("This design was made before the Fountain Designer; save it from the form."))
    fields = _parse(fields_json)
    if name:
        _require("write")
        doc = frappe.get_doc(DESIGN_DOCTYPE, name)
        if doc.docstatus != 0:
            frappe.throw(_("{0} is issued, so it can no longer change. Amend it to make a new revision.").format(doc.name))
    else:
        _require("create")
        doc = frappe.new_doc(DESIGN_DOCTYPE)
    for key in DESIGN_FIELDS:
        if key in fields:
            doc.set(key, fields.get(key) or None)
    doc.model_json = json.dumps(model)
    doc.mode = "Full" if str(model.get("mode") or "").lower() == "full" else "Quick"
    doc.save()
    return {"name": doc.name, "status": doc.status, "mode": doc.mode, "modified": str(doc.modified),
            "design_title": doc.get("design_title")}


# ------------------------------------------------------------------- email


# What each paper is called in an email's subject and body.
PRINT_LABELS = {pf: label for _k, label, pf in prints_mod.OUTPUTS}
PRINT_LABELS[prints_mod.PF_PACKET] = "Submittal packet"
# The papers whose email may quote the budget: the client's report and the proposal itself.
BUDGET_PAPERS = {prints_mod.PF_REPORT, prints_mod.PF_BUDGET}


def _customer_email(doc):
    """The customer's address the way the prints find it (``print_lookup.party_details``: the
    customer's primary contact, its address, its own fields), or ``""``."""
    if not doc.get("customer"):
        return ""
    try:
        from erpnext_enhancements.print_lookup import party_details

        return party_details(doc).get("email") or ""
    except Exception:
        return ""


def email_body(doc, print_format, label):
    """The email's body in the email design system's chrome (``email_style.wrap``, the Design pillar),
    with no container of its own (docs/email-design-system.md)."""
    from erpnext_enhancements import email_style as es

    title = doc.get("design_title") or doc.name
    rollups = (stored_json(doc).get("rollups") or {})
    facts = [(_("Design"), f"{title} ({doc.name})")]
    flow = rollups.get("operating_flow_gpm") or rollups.get("design_flow_gpm")
    if flow:
        facts.append((_("Flow"), prints_mod.fnum(flow, 0, "gpm")))
    if rollups.get("total_basin_gallons"):
        facts.append((_("Water in the system"), prints_mod.fnum(rollups["total_basin_gallons"], 0, "gallons")))
    if print_format in BUDGET_PAPERS and doc.get("budget_total"):
        facts.append((_("Budget (before contingency)"), prints_mod.money(doc.get("budget_total"), cents=False)))
    body = (
        es.p(_("Attached is the {0} for {1}.").format(_(label).lower(), title))
        + es.kv(facts)
        + es.note(_("The figures are design estimates from Sapphire Fountains' Fountain Designer."))
    )
    return es.wrap(body, title=f"{_(label)}: {title}", eyebrow=_(label), pillar="design", tagline=True)


def stored_json(doc):
    raw = doc.get("results_json")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _print_pdf(doc, print_format):
    """The paper as a PDF File on the design (private), for a composer with no form to print from."""
    pdf = frappe.get_print(DESIGN_DOCTYPE, doc.name, print_format=print_format, as_pdf=True, pdf_generator="chrome")
    if not pdf:
        frappe.throw(_("The PDF could not be made. Try again, or print it from the form."))
    safe = str(doc.name).replace("/", "-")
    slug = print_format.split(" - ", 1)[-1].lower().replace(" ", "-")
    file_doc = frappe.get_doc({
        "doctype": "File",
        # "-print-" marks a paper made for an email, so the packet never merges it as a CAD drawing.
        "file_name": f"{safe}-print-{slug}.pdf",
        "content": pdf,
        "is_private": 1,
        "attached_to_doctype": DESIGN_DOCTYPE,
        "attached_to_name": doc.name,
    }).insert(ignore_permissions=True)
    return {"name": file_doc.name, "file_name": file_doc.file_name, "file_url": file_doc.file_url}


@frappe.whitelist(methods=["POST"])
def email_draft(name, print_format=None, attach_pdf=0):
    """The start of an email that sends one of the design's papers (README, "email_draft"): the subject,
    the customer's address, the body in the email chrome, and the formats to choose from. With
    ``attach_pdf`` (the page, whose composer has no form to print from) the paper is made into a PDF File
    on the design. Nothing is sent here: the person sends from frappe's composer, which links the email
    to the design's timeline."""
    _require("read")
    doc = frappe.get_doc(DESIGN_DOCTYPE, name)
    doc.check_permission("read")
    if not frappe.has_permission(DESIGN_DOCTYPE, "email", doc=doc):
        frappe.throw(_("You are not allowed to email Water Feature Designs."), frappe.PermissionError)
    print_format = print_format or prints_mod.PF_REPORT
    if print_format not in PRINT_LABELS:
        frappe.throw(_("{0} is not one of the design's papers.").format(print_format))
    label = PRINT_LABELS[print_format]
    title = doc.get("design_title") or doc.name
    out = {
        "subject": f"{title}: {_(label)} (#{doc.name})",
        "recipients": _customer_email(doc),
        "message": email_body(doc, print_format, label),
        "print_format": print_format,
        "formats": [{"print_format": pf, "label": _(PRINT_LABELS[pf])} for pf in prints_mod.PRINT_FORMATS],
        "attachment": None,
    }
    if str(attach_pdf) in ("1", "true", "True"):
        out["attachment"] = _print_pdf(doc, print_format)
    return out


# --------------------------------------------------------------- sign-offs


@frappe.whitelist(methods=["POST"])
def acknowledge_issue(design, issue_key, note=None):
    """Sign off one open warning: a Water Design Issue Ack row and an entry in ``model.acks``, so
    the next derive marks the issue acknowledged. Refuses an issue that is not open, one that is
    not a warning, and a design that is not a draft."""
    _require("write")
    doc = frappe.get_doc(DESIGN_DOCTYPE, design)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is issued; its sign-offs are part of the record and cannot change.").format(doc.name))
    try:
        live = {i.get("key"): i for i in json.loads(doc.get("design_issues_json") or "[]")}
    except ValueError:
        live = {}
    issue = live.get(issue_key)
    if not issue:
        frappe.throw(_("That issue is no longer open on this design. Reload to see the current list."))
    if issue.get("severity") != "warning":
        frappe.throw(_("Only a warning can be signed off; a blocking issue must be fixed."))
    model = stored_model(doc) or {}
    acks = [a for a in model.get("acks") or [] if isinstance(a, dict) and a.get("issue_key") != issue_key]
    now = frappe.utils.now_datetime()
    acks.append({"issue_key": issue_key, "note": note or "", "by": frappe.session.user, "on": str(now)})
    model["acks"] = acks
    doc.model_json = json.dumps(model)
    if issue_key not in {a.issue_key for a in doc.get("issue_acks") or []}:
        doc.append("issue_acks", {
            "issue_key": issue_key,
            "title": (issue.get("title") or "")[:140],
            "acknowledged_by": frappe.session.user,
            "acknowledged_on": now,
            "note": note,
        })
    doc.save()
    return {"acks": acks}


@frappe.whitelist()
def run_calc(calc, inputs=None):
    """The stateless calculator (the wizard's and the AI tool's), kept as it is."""
    from erpnext_enhancements.water_engineering.api.water_design import run_calc as _run_calc

    return _run_calc(calc, inputs)


# ------------------------------------------------------------------ catalog


@frappe.whitelist()
def catalog_search(equipment_class=None, q=None):
    """Typeahead over the catalog: Items of the class (nozzles: the Nozzle Profiles), up to 12."""
    _require("read")
    cls = next((c for c in EQUIPMENT_CLASSES if c["key"] == equipment_class), None)
    if not cls:
        return []
    text = f"%{(q or '').strip()}%"
    if cls["key"] == "nozzle":
        try:
            rows = frappe.get_list("Nozzle Profile", filters=[["profile_name", "like", text]],
                                   fields=["name", "item", "profile_name", "nozzle_type", "is_generic_estimate"],
                                   limit_page_length=12)
        except Exception:
            return []
        return [{"item": r.get("item") or r.get("name"), "item_name": r.get("profile_name"), "description": r.get("nozzle_type") or "",
                 "generic": bool(r.get("is_generic_estimate")), "source": "generic estimate" if r.get("is_generic_estimate") else "datasheet",
                 "equipment_class": "nozzle", "nozzle_profile": r.get("name")} for r in rows]
    meta = frappe.get_meta("Item")
    if not meta.has_field("custom_equipment_class"):
        return []
    fields = ["name", "item_name", "description"]
    if meta.has_field("custom_generic_estimate"):
        fields.append("custom_generic_estimate")
    filters = [["disabled", "=", 0], ["custom_equipment_class", "=", cls["item_class"]]]
    or_filters = [["name", "like", text], ["item_name", "like", text]]
    try:
        rows = frappe.get_list("Item", filters=filters, or_filters=or_filters, fields=fields, limit_page_length=12)
    except Exception:
        return []
    return [{"item": r["name"], "item_name": r.get("item_name"), "description": frappe.utils.strip_html(r.get("description") or "")[:200],
             "generic": bool(r.get("custom_generic_estimate")),
             "source": "generic estimate" if r.get("custom_generic_estimate") else "datasheet",
             "equipment_class": cls["key"]} for r in rows]


# -------------------------------------------------------------------- push


def _text_item():
    try:
        return frappe.db.get_single_value(SETTINGS_DOCTYPE, "designer_text_item") or None
    except Exception:
        return None


def _push_rows(doc):
    """The design's BOM split into item rows and text rows; with a Text-line Item set in Water
    Engineering Settings, a text row goes as that Item with the line's own description."""
    text_item = _text_item()
    items, texts = [], []
    for row in doc.get("bom") or []:
        qty = float(row.qty or 0)
        if qty <= 0:
            continue
        line = {"qty": qty, "rate": float(row.rate or 0), "description": row.description or row.item or "",
                "uom_note": row.uom}
        if row.item:
            items.append(dict(line, item_code=row.item))
        elif text_item:
            # A placeholder Item carries no unit conversions: the line goes in its stock UOM at the
            # designed quantity with the designed unit in the description, never flagged a mismatch.
            unit = f" ({qty:g} {row.uom})" if row.uom else ""
            texts.append(dict(line, item_code=text_item, uom_note=None, description=line["description"] + unit))
        else:
            texts.append(dict(line, item_code=None, uom_note=row.uom))
    return items, texts


# The engine's units and the ERPNext UOM each names.
ENGINE_UOMS = {"ea": "Nos", "each": "Nos", "nos": "Nos", "ft": "Foot", "foot": "Foot", "hr": "Hour", "hour": "Hour",
               "gal": "Gallon Liquid (US)", "lb": "Pound", "cubic yard": "Cubic Yard", "yd3": "Cubic Yard",
               "ton": "Ton (Short)", "sq ft": "Square Foot", "sqft": "Square Foot"}


def _uoms(rows):
    """``{item: {"stock": stock_uom, "factors": {uom: conversion_factor}}}`` for the push rows."""
    codes = sorted({r["item_code"] for r in rows if r.get("item_code")})
    if not codes:
        return {}
    out = {}
    try:
        for r in frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "stock_uom"]):
            out[r["name"]] = {"stock": r.get("stock_uom"), "factors": {}}
        for r in frappe.get_all("UOM Conversion Detail", filters={"parent": ["in", codes], "parenttype": "Item"},
                                fields=["parent", "uom", "conversion_factor"]):
            if r.get("parent") in out and r.get("conversion_factor"):
                out[r["parent"]]["factors"][r.get("uom")] = r["conversion_factor"]
    except Exception:
        pass
    return out


def _item_row(row, uoms, **extra):
    """One pushed item row in the unit the design counted it in. The engine's unit becomes the ERPNext
    UOM it names (``ft`` -> Foot): the stock UOM itself, or one of the Item's UOM conversions, goes
    with its factor. A unit the Item has no conversion for goes in the stock UOM at the designed
    quantity with the description saying so, never silently relabeled (11 ft is not 11 sticks).
    Returns (row, True when the unit could not be matched)."""
    out = {"item_code": row["item_code"], "qty": row["qty"], "description": row["description"], **extra}
    info = uoms.get(row["item_code"]) or {}
    stock = info.get("stock")
    if not stock:
        return out, False
    if not row.get("uom_note"):  # a text line on the placeholder Item: its stock UOM, as written
        out.update(uom=stock, stock_uom=stock, conversion_factor=1)
        return out, False
    wanted = ENGINE_UOMS.get(str(row.get("uom_note")).strip().lower(), row.get("uom_note"))
    if wanted == stock:
        out.update(uom=stock, stock_uom=stock, conversion_factor=1)
        return out, False
    factor = info.get("factors", {}).get(wanted)
    if factor:
        out.update(uom=wanted, stock_uom=stock, conversion_factor=factor)
        return out, False
    out.update(uom=stock, stock_uom=stock, conversion_factor=1)
    out["description"] = (f"{row['description']} (designed as {row['qty']:g} {row.get('uom_note')}; "
                          f"no conversion to {stock} on the Item: check the quantity)")
    return out, True


def _company():
    return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")


def _slug(doctype):
    return doctype.lower().replace(" ", "-")


def _design_for_push(name):
    _require("read")
    doc = frappe.get_doc(DESIGN_DOCTYPE, name)
    doc.check_permission("read")
    if not doc.get("bom"):
        frappe.throw(_("{0} has no bill of materials yet. Save the design so the engine can build one.").format(doc.name))
    return doc


@frappe.whitelist(methods=["POST"])
def push_quotation(name, quotation=None):
    """The design's BOM to a Quotation: a new draft, or lines added to the draft ``quotation``."""
    doc = _design_for_push(name)
    items, texts = _push_rows(doc)
    if quotation:
        qtn = frappe.get_doc("Quotation", quotation)
        qtn.check_permission("write")
        if qtn.docstatus != 0:
            frappe.throw(_("{0} is already submitted. Pick a draft Quotation, or leave it blank for a new one.").format(qtn.name))
    else:
        if not frappe.has_permission("Quotation", "create"):
            frappe.throw(_("You do not have permission to create a Quotation."), frappe.PermissionError)
        qtn = frappe.new_doc("Quotation")
        if doc.get("opportunity"):
            opp = frappe.db.get_value("Opportunity", doc.opportunity, ["opportunity_from", "party_name"], as_dict=True) or {}
            qtn.quotation_to = opp.get("opportunity_from") or "Customer"
            qtn.party_name = opp.get("party_name")
            qtn.opportunity = doc.opportunity
        elif doc.get("customer"):
            qtn.quotation_to = "Customer"
            qtn.party_name = doc.customer
        else:
            frappe.throw(_("Link the design to a customer or an opportunity before making a Quotation."))
        qtn.company = _company()
        if qtn.meta.has_field("project") and doc.get("project"):
            qtn.project = doc.project
    rows = items + [t for t in texts if t["item_code"]]
    uoms = _uoms(rows)
    unmatched = 0
    for row in rows:
        built, odd = _item_row(row, uoms, rate=row["rate"])
        qtn.append("items", built)
        unmatched += odd
    loose = [t for t in texts if not t["item_code"]]
    if loose:
        listing = "".join(f"<li>{frappe.utils.escape_html(t['description'])} ({t['qty']:g} {frappe.utils.escape_html(t['uom_note'] or '')})</li>"
                          for t in loose)
        qtn.terms = (qtn.get("terms") or "") + f"<p>{_('To be selected from {0}:').format(doc.name)}</p><ul>{listing}</ul>"
    if not qtn.get("items"):
        frappe.throw(_("None of the design's lines has an Item yet, so there is nothing to quote. Pick Items, or set a Text-line Item in Water Engineering Settings."))
    qtn.save()
    return {"doctype": "Quotation", "name": qtn.name, "url": f"/desk/{_slug('Quotation')}/{qtn.name}",
            "lines": len(items), "text_lines": len(texts), "unit_mismatches": unmatched}


@frappe.whitelist(methods=["POST"])
def push_material_request(name):
    """The design's BOM Items to a draft purchase Material Request. A line with no Item goes only
    when a Text-line Item is set; otherwise it is counted and left off."""
    doc = _design_for_push(name)
    if not frappe.has_permission("Material Request", "create"):
        frappe.throw(_("You do not have permission to create a Material Request."), frappe.PermissionError)
    items, texts = _push_rows(doc)
    rows = items + [t for t in texts if t["item_code"]]
    if not rows:
        frappe.throw(_("None of the design's lines has an Item yet, so there is nothing to request."))
    schedule = frappe.utils.add_days(frappe.utils.nowdate(), 7)
    mr = frappe.new_doc("Material Request")
    mr.material_request_type = "Purchase"
    mr.company = _company()
    mr.schedule_date = schedule
    uoms = _uoms(rows)
    unmatched = 0
    for row in rows:
        built, odd = _item_row(row, uoms, schedule_date=schedule, project=doc.get("project") or None)
        mr.append("items", built)
        unmatched += odd
    mr.save()
    return {"doctype": "Material Request", "name": mr.name, "url": f"/desk/{_slug('Material Request')}/{mr.name}",
            "lines": len(items), "text_lines": len(texts), "unit_mismatches": unmatched}
