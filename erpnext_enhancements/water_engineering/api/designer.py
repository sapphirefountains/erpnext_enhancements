"""Whitelisted endpoints for the Fountain Designer page (``page/fountain_designer``).

The contract is ``public/js/fountain_designer/README.md``, "The contract", field for field;
``docs/fountain-designer/PLAN.md`` section 3.4 is the plan behind it. Every endpoint is a thin
adapter: the ladder is ``water_engineering.derive.derive_model`` (pure, bench-free) and this
module only hands it what lives in the database — the house rules on Water Engineering Settings,
the pump catalog from phase D's ``catalog_reader``, Item prices, and the geocoded address.

``boot``, ``get_design`` and ``catalog_search`` are called as GET, so none of them is
``methods=["POST"]``. Permissions gate on the ``Water Feature Design`` doctype (read to look, write to
save); ``doc.save()`` enforces the document-level permission on every mutation.

Since v1.771.0 (phase H) this module also holds what outlived the Water Engineering Wizard and the old
``api/water_design.py``: the page's access check (``check_permission``), the stateless calculator
(``run_calc`` over ``water_engineering.calculator``, the page's Calculator drawer and the ``water_calc``
AI tool), the Item form's pump-curve panel (``item_pump_curves``), and the helpers the
``save_water_design`` and ``water_design_status`` AI tools call (``tool_save``, ``design_status``,
``merge_legacy_payload``). ``api/water_design.py`` keeps the Control Panel Design reader and thin
re-exports of the moved endpoints for an old Desk tab.
"""

import copy
import hashlib
import json
import math
from urllib.parse import urlencode

import frappe
from frappe import _

from erpnext_enhancements.water_engineering import calculator as calc_mod
from erpnext_enhancements.water_engineering import catalog_reader
from erpnext_enhancements.water_engineering import derive as derive_mod
from erpnext_enhancements.water_engineering import issues as issues_mod
from erpnext_enhancements.water_engineering import prints as prints_mod
from erpnext_enhancements.water_engineering.engine import feature_visual_kind
from erpnext_enhancements.water_engineering.engine import kits as kits_engine
from erpnext_enhancements.water_engineering.engine.data import jurisdictions as jurisdictions_data
from erpnext_enhancements.water_engineering.engine import model as model_engine
from erpnext_enhancements.water_engineering.engine import pipe as pipe_engine
from erpnext_enhancements.water_engineering.engine.data.pipe_specs import PIPE_SPECS
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
# Phase J's point lookups, cached a month per point like the geocode (a failure ten minutes). The
# USGS address in the phase brief (/ws/designmaps/asce7-22.json) answers 301 to this one by way of
# plain http, so the current one is called directly.
USGS_URL = "https://earthquake.usgs.gov/ws/building-codes/asce7-22/calculate"
# Three lookups run one after another inside a save, so each waits 5 s at most (15 s when all fail; the
# failure is then cached ten minutes, so the next save does not wait again).
LOOKUP_TIMEOUT_S = 5
USGS_TIMEOUT_S = LOOKUP_TIMEOUT_S
CIT_USGS = ("USGS Seismic Design Web Services, ASCE 7-22, Risk Category II, Site Class D "
            "(https://earthquake.usgs.gov/ws/building-codes/asce7-22/calculate)")
CIT_ELEVATION = "Google Maps Elevation API at the geocoded point"
CIT_GEOCODE = "Google Geocoding API (the site's address)"
SEISMIC_FIELDS = ("seismic_sdc", "sds", "sd1")

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
    # Filled at boot from the jurisdiction table (jurisdiction_options): Utah first, then by state name.
    "jurisdictions": [],
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


@frappe.whitelist()
def check_permission():
    """True if the caller may open the Fountain Designer (read on Water Feature Design). The page's gate,
    asked before ``boot``; it was ``api/water_design.check_permission`` until v1.771.0."""
    return bool(frappe.has_permission(DESIGN_DOCTYPE, "read"))


# ---------------------------------------------- catalog readers (pumps, nozzles)


def nozzle_profile_params(profile_name):
    """A Nozzle Profile's orifice and rated figures for the engine's ``nozzle_flow``, with its type,
    generic flag and performance ``points`` (``catalog_reader.nozzle_profiles``); {} if it is missing."""
    if not profile_name:
        return {}
    p = catalog_reader.nozzle_profiles([profile_name]).get(profile_name)
    if not p:
        return {}
    return {
        "cd": p.get("discharge_coefficient"),
        "orifice_diameter_in": p.get("orifice_diameter_in"),
        "orifice_area_in2": p.get("orifice_area_in2"),
        "rated_gpm": p.get("rated_gpm"),
        "rated_head_ft": p.get("rated_head_ft"),
        "nozzle_type": p.get("nozzle_type"),
        "level_sensitive": p.get("level_sensitive"),
        "is_generic_estimate": p.get("is_generic_estimate"),
        "points": p.get("points") or [],
    }


def pump_curves(item_codes):
    """{item_code: [{flow_gpm, head_ft}, ...]}: each pump Item's best curve (full speed, sourced, largest
    impeller), else its legacy ``custom_pump_curve`` table. Items with no curve are absent.
    ``catalog_reader.pump_curve_sets`` has every curve, with efficiency, NPSHr and provenance."""
    out = {}
    for item_code, curves in catalog_reader.pump_curve_sets(item_codes).items():
        points = curves[0]["points"] if curves else []
        if points:
            out[item_code] = [{"flow_gpm": p["flow_gpm"], "head_ft": p["head_ft"]} for p in points]
    return out


@frappe.whitelist()
def item_pump_curves(item_code):
    """The Item form's Pump Curves panel (``public/js/water_engineering/pump_curve_chart.js``): this
    Item's curves, best first, with points. A user who cannot read Pump Curves gets an empty list and
    ``can_read`` False rather than an error, so the Item form still opens for them."""
    if not item_code or not frappe.has_permission("Pump Curve", "read"):
        return {"curves": [], "can_read": False, "can_create": False}
    frappe.has_permission("Item", "read", doc=item_code, throw=True)
    curves = catalog_reader.pump_curve_sets([item_code]).get(item_code, [])
    return {
        "curves": curves,
        "can_read": True,
        "can_create": bool(frappe.has_permission("Pump Curve", "create")),
    }


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


def _cached(key, fetch):
    """``fetch()`` -> ``(value, definite)`` behind the cache: a definite answer (a value, or a definite "no")
    for a month, anything else ten minutes, so a timeout or a rate limit is asked again soon."""
    cache = frappe.cache()
    hit = cache.get_value(key)
    if hit is not None:
        return hit or None
    try:
        value, definite = fetch()
    except Exception:
        value, definite = None, False
    cache.set_value(key, value or {}, expires_in_sec=GEOCODE_CACHE_S if definite else GEOCODE_RETRY_S)
    return value or None


def _engine_may_fill(site, key):
    """True when the derive may write ``site[key]``: it is blank, or the engine filled it and nobody has
    changed it since (``derive.engine_owned``)."""
    if site.get(key) not in (None, ""):
        return derive_mod.engine_owned(site, key) is not None
    return True


def _point_key(kind, lat, lng):
    return f"fountain_designer:{kind}:{float(lat):.4f},{float(lng):.4f}"


def _valid_point(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    return bool(lat or lng) and -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def geocode_lookup(address):
    """``{lat, lng}`` for an address, plus ``jurisdiction`` (the state's code) when it is in the United
    States, or None. Google's ZERO_RESULTS is a definite answer. Elsewhere there is no state to put the
    design in, so the site keeps "No jurisdiction data"."""
    def fetch():
        from erpnext_enhancements.workforce.sites import geocode_text

        found, problem = geocode_text(address, timeout=LOOKUP_TIMEOUT_S)
        point = {"lat": found.get("lat"), "lng": found.get("lng")} if found else {}
        state = str((found or {}).get("state") or "").strip().upper()
        if state and str((found or {}).get("country") or "").strip().upper() == "US":
            point["jurisdiction"] = state
        return point, bool(found) or str(problem or "").upper().startswith("ZERO_RESULTS")

    # "v2": an answer cached before v1.774.0 has no state in it.
    key = "fountain_designer:geocode:v2:" + hashlib.sha1(address.lower().encode("utf-8")).hexdigest()
    return _cached(key, fetch)


def elevation_lookup(lat, lng):
    """``{"elevation_ft": ft}`` at a point from Google's Elevation API (``workforce.sites.elevation_at``), or None."""
    def fetch():
        from erpnext_enhancements.workforce.sites import elevation_at

        feet, _problem = elevation_at(lat, lng, timeout=LOOKUP_TIMEOUT_S)
        return ({"elevation_ft": feet} if feet is not None else {}), feet is not None

    return _cached(_point_key("elevation", lat, lng), fetch)


def seismic_lookup(lat, lng):
    """``{"seismic_sdc", "sds", "sd1"}`` at a point from the USGS ASCE 7-22 service (Risk Category II, Site
    Class D: the house's default site class until a geotechnical report says otherwise), or None. Never
    raises or logs: a failure falls back to the state's default category."""
    def fetch():
        import requests

        response = requests.get(USGS_URL, params={"latitude": float(lat), "longitude": float(lng), "riskCategory": "II",
                                                  "siteClass": "D", "title": "Sapphire Fountain Designer"},
                                timeout=USGS_TIMEOUT_S)
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        body = payload.get("response") if isinstance(payload, dict) else None
        if isinstance(body, list):
            body = body[0] if body else None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return {}, False
        out = {}
        sdc = str(data.get("sdc") or "").strip().upper()
        if sdc in ("A", "B", "C", "D", "E", "F"):
            out["seismic_sdc"] = sdc
        for key in ("sds", "sd1"):
            try:
                v = float(data.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(v) and v >= 0:
                out[key] = round(v, 3)
        return out, bool(out)

    return _cached(_point_key("seismic", lat, lng), fetch)


def site_lookup(site):
    """What the address tells the derive (``derive.derive_site``): the geocoded point when the site has
    none and the address's state when it has none, then at that point Google's elevation and the USGS
    seismic design values, each only where the derive may still fill the field (blank, or filled by the
    engine before). ``cites`` names each one's source. Every lookup is cached a month (a failure ten
    minutes) and none raises or logs: a failure leaves the state's default standing. The controller calls
    this on a save, never the page's per-keystroke derive, so a half-typed address costs nothing. A pick
    from the page's address suggestions sends the point and the state along, so it geocodes nothing."""
    site = site or {}
    out, cites = {}, {}
    address = str(site.get("address") or "").strip()
    lat, lng = site.get("lat"), site.get("lng")
    need_point = lat in (None, "") or lng in (None, "")
    need_state = not str(site.get("jurisdiction") or "").strip()
    if address and (need_point or need_state):
        found = geocode_lookup(address) or {}
        if need_point and _valid_point(found.get("lat"), found.get("lng")):
            out.update(lat=found["lat"], lng=found["lng"])
            cites.update(lat=CIT_GEOCODE, lng=CIT_GEOCODE)
            lat, lng = found["lat"], found["lng"]
        if found.get("jurisdiction") and _engine_may_fill(site, "jurisdiction"):
            out["jurisdiction"] = found["jurisdiction"]
            cites["jurisdiction"] = CIT_GEOCODE
    if _valid_point(lat, lng):
        if _engine_may_fill(site, "elevation_ft"):
            found = elevation_lookup(lat, lng)
            if found:
                out.update(found)
                cites["elevation_ft"] = CIT_ELEVATION
        if any(_engine_may_fill(site, k) for k in SEISMIC_FIELDS):
            found = seismic_lookup(lat, lng)
            if found:
                out.update(found)
                cites.update({k: CIT_USGS for k in found})
    if not out:
        return None
    out["cites"] = cites
    return out


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


def jurisdiction_options():
    """The Site step's picker: every state in the jurisdiction table as ``{value, label}``, Utah first and
    then by name (``engine/data/jurisdictions.py``)."""
    return [{"value": code, "label": f"{name} ({code})"} for code, name in jurisdictions_data.names().items()]


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
        "options": dict(copy.deepcopy(OPTIONS), jurisdictions=jurisdiction_options()),
        "catalogs": {"equipment_classes": [{"key": c["key"], "label": c["label"]} for c in EQUIPMENT_CLASSES],
                     "slot_classes": dict(SLOT_CLASSES)},
        "house_rules": house_rules(),
        "prints": print_formats(),
        # The Review step's Calculator drawer: every calc with its inputs (water_engineering/calculator.py).
        "calculator": calc_mod.catalog(),
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
                    "blocker_count", "warning_count", "issue_ready", "budget_total", "bom_subtotal", "modified"],
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
    # The site as the save's derive left it: the page's own derive never looks anything up, so this is how
    # the elevation and seismic values a save found reach the page (model.merge_site).
    site = (stored_model(doc) or {}).get("site")
    return {"name": doc.name, "status": doc.status, "mode": doc.mode, "modified": str(doc.modified),
            "design_title": doc.get("design_title"), "site": site if isinstance(site, dict) else None}


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
    """The email's starting text for frappe's composer: plain paragraphs, because the composer is a
    rich-text editor that strips a ``<style>`` block and mangles the email shell's tables and MSO comments.
    The shell (``email_style.wrap``, the Design pillar) goes on when the email is sent:
    ``water_engineering.email_hooks.wrap_design_email``."""
    title = doc.get("design_title") or doc.name
    rollups = (stored_json(doc).get("rollups") or {})
    facts = [(_("Design"), f"{title} ({doc.name})")]
    flow = rollups.get("operating_flow_gpm") or rollups.get("design_flow_gpm")
    if flow:
        facts.append((_("Flow"), prints_mod.fnum(flow, 0, "gpm")))
    if rollups.get("total_basin_gallons"):
        facts.append((_("Water in the system"), prints_mod.fnum(rollups["total_basin_gallons"], 0, "gallons")))
    if print_format in BUDGET_PAPERS and doc.get("budget_total"):
        # The same figure as the paper's Budget: the BOM subtotal plus the contingency.
        pct = ((stored_json(doc).get("paper") or {}).get("budget_rules") or {}).get("contingency_pct")
        budget_label = _("Budget (with {0}% contingency)").format(prints_mod.fnum(pct, 0)) if pct else _("Budget")
        facts.append((budget_label, prints_mod.money(doc.get("budget_total"), cents=False)))
    esc = frappe.utils.escape_html
    return (
        f"<p>{esc(_('Attached is the {0} for {1}.').format(_(label).lower(), title))}</p>"
        "<p>" + "<br>".join(f"<strong>{esc(k)}:</strong> {esc(v)}" for k, v in facts) + "</p>"
        f"<p><em>{esc(_('The figures are design estimates from Sapphire Fountains’ Fountain Designer.'))}</em></p>"
    )


def stored_json(doc):
    """``results_json`` parsed, or {}."""
    return _json_value(doc.get("results_json"), dict)


def _print_pdf(doc, print_format):
    """The paper as a PDF File on the design (private), for a composer with no form to print from. The
    one made since the design last changed is reused, so sending twice does not pile up attachments."""
    safe = str(doc.name).replace("/", "-")
    slug = print_format.split(" - ", 1)[-1].lower().replace(" ", "-")
    try:
        made = frappe.get_all("File", filters={"attached_to_doctype": DESIGN_DOCTYPE, "attached_to_name": doc.name,
                                               "file_name": ["like", f"{safe}-print-{slug}%.pdf"],
                                               "creation": [">=", doc.get("modified")]},
                              fields=["name", "file_name", "file_url"], order_by="creation desc", limit=1)
    except Exception:
        made = []
    if made:
        return {"name": made[0]["name"], "file_name": made[0]["file_name"], "file_url": made[0]["file_url"]}
    pdf = frappe.get_print(DESIGN_DOCTYPE, doc.name, print_format=print_format, as_pdf=True, pdf_generator="chrome")
    if not pdf:
        frappe.throw(_("The PDF could not be made. Try again, or print it from the form."))
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
    # The papers this site has, as boot().prints lists them for the page: one it could not seed, or that
    # is disabled, is neither offered nor accepted (the composer would ask frappe for a missing format).
    have = [pf for pf in prints_mod.PRINT_FORMATS if pf in {r["print_format"] for r in print_formats()}]
    if not have:
        frappe.throw(_("No design papers are set up on this site yet. Run a migrate, then try again."))
    print_format = print_format or (prints_mod.PF_REPORT if prints_mod.PF_REPORT in have else have[0])
    if print_format not in PRINT_LABELS:
        frappe.throw(_("{0} is not one of the design's papers.").format(print_format))
    label = PRINT_LABELS[print_format]
    if print_format not in have:
        frappe.throw(_("{0} is not set up on this site yet. Pick another paper.").format(_(label)))
    title = doc.get("design_title") or doc.name
    out = {
        "subject": f"{title}: {_(label)} (#{doc.name})",
        "recipients": _customer_email(doc),
        "message": email_body(doc, print_format, label),
        "print_format": print_format,
        "formats": [{"print_format": pf, "label": _(PRINT_LABELS[pf])} for pf in have],
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


def _run_calc(calc, inputs):
    """One calculation's ``CalcResult`` dict (``calculator.run`` with the site's Nozzle Profiles). No
    permission check: ``run_calc`` and the ``water_calc`` AI tool gate before calling it."""
    if not isinstance(inputs, dict):
        # As the wizard's endpoint did: inputs that do not read as an object run on the defaults.
        try:
            inputs = json.loads(inputs) if inputs else {}
        except (TypeError, ValueError):
            inputs = {}
        inputs = inputs if isinstance(inputs, dict) else {}
    try:
        return calc_mod.run(calc, inputs, nozzle_lookup=nozzle_profile_params)
    except calc_mod.UnknownCalc:
        frappe.throw(_("Unknown calculation: {0}").format(calc), frappe.ValidationError)


@frappe.whitelist()
def run_calc(calc, inputs=None):
    """The stateless calculator (the Review step's Calculator drawer): one engine calculation and its
    math, the formula, steps, citations, warnings and options (``boot().calculator`` lists them)."""
    _require("read")
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


# ------------------------------------------------------------- the AI tools
# save_water_design and water_design_status (assistant_tools/) call these. They read and write the
# record the page does, so the assistant and the page agree on every number (v1.771.0, phase H).

# A schema-1 payload's design inputs that have a place in a schema-2 model: (section, key).
_GLOBAL_INPUT_PATHS = {
    "venue_type": ("site", "venue_type"),
    "governing_code": ("site", "governing_code"),
    "pipe_material": ("pump_room", "material"),
}
# The tables of the old payload the Fountain Designer derives for itself.
DERIVED_TABLES = ("electrical_loads", "venue_fixtures", "design_equipment")


def merge_legacy_payload(existing, tables, global_inputs):
    """A schema-1 payload laid onto an existing schema-2 model, table by table, as the old tool
    promised ("each list REPLACES that table"): only the sections the payload names change, and
    each one it changes is pinned ``custom`` so the next derive keeps it. Returns (model, ignored),
    ``ignored`` naming every input there is no place for, so the caller can say so instead of
    dropping it in silence. (In ``api/water_design.py`` until v1.771.0.)"""
    model = model_engine.from_dict(existing).to_dict()
    setup = model.setdefault("setup", {})
    ignored = []
    legacy = {t: rows for t, rows in tables.items() if t in ("basins", "features", "tiers", "pipe_segments", "pumps")}
    legacy["global_inputs"] = dict(global_inputs or {})
    legacy.setdefault("basins", [])
    if "pipe_segments" in tables and "features" not in tables and model.get("features"):
        # The old shape has no topology: its segments are laid from the feature list, so a segment list
        # alone cannot be placed against the features the Designer drew.
        legacy.pop("pipe_segments")
        ignored.append("pipe_segments (send features with them, or edit the piping in the Fountain Designer)")
    migrated = model_engine.migrate(legacy)
    if "basins" in tables:
        model["basins"] = migrated["basins"]
        setup["basin"] = "custom"
    if "features" in tables:
        model["features"] = migrated["features"]
        setup["features"] = "custom"
    elif "tiers" in tables:
        ignored.append("tiers (send them with the features they belong to)")
    # A feature caught by no basin the model has (new basins, or features sent without their basins,
    # which migrate leaves unattached) lands in the first basin, as the old engine read it.
    ids = [b.get("id") for b in model.get("basins") or []]
    for f in model.get("features") or []:
        if f.get("basin") not in ids:
            f["basin"] = ids[0] if ids else None
    if "pipe_segments" in legacy:
        old_pumps = (model.get("network") or {}).get("pumps") or []
        model["network"] = migrated["network"]
        setup["piping"] = "custom"
        if "pumps" not in tables:
            # New piping is not a new pump: each pump of the re-laid network keeps the stored one's pick,
            # curve, control and the rest, under the new network's own id (its nodes name that id).
            for new, old in zip(model["network"].get("pumps") or [], old_pumps):
                new.update({k: copy.deepcopy(v) for k, v in old.items() if k != "id"})
    if "pumps" in tables:
        picks = [e for e in migrated.get("equipment") or [] if e.get("slot") == "pump"]
        model["equipment"] = [e for e in model.get("equipment") or [] if e.get("slot") != "pump"] + picks
        pumps = (model.get("network") or {}).get("pumps") or []
        if picks and pumps:
            # The curve goes with the Item: naming it lets the derive load that pump's own curve
            # (derive.resolve_curves), rather than solve the new pump on the old pick's curve.
            pumps[0]["item"] = picks[0]["item"]
            pumps[0]["curve"] = picks[0]["item"]
            for key in ("hp", "kw", "shutoff_head_ft", "max_flow_gpm", "motor_eff_pct", "catalog_source"):
                pumps[0].pop(key, None)
            setup["pump"] = "custom"
    for table in DERIVED_TABLES:
        if table in tables:
            ignored.append(f"{table} (the Fountain Designer derives these)")
    for key, value in (global_inputs or {}).items():
        path = _GLOBAL_INPUT_PATHS.get(key)
        if path:
            model.setdefault(path[0], {})[path[1]] = value
        else:
            ignored.append(key)
    return model, ignored


def tool_model(existing, model=None, tables=None, global_inputs=None):
    """The model ``save_water_design`` saves, and what it ignored: ``(model, ignored)``.

    * ``model`` (schema 2) is the whole design and replaces the stored one; ``tables`` and
      ``global_inputs`` are then ignored, and said so. A model with no ``acks`` keeps the stored
      sign-offs (and says so): leaving them out of an edited model is not withdrawing them.
    * Otherwise the old payload: onto an existing schema-2 design through ``merge_legacy_payload``
      (only the tables it names change), a stored schema-1 model being converted with
      ``engine.model.migrate`` first (keeping its sign-offs); for a new design, the payload alone
      through ``migrate`` into a fresh model.
    """
    tables = {t: rows for t, rows in (tables or {}).items() if rows is not None}
    global_inputs = dict(global_inputs or {})
    if model:
        model = model if isinstance(model, dict) else _parse(model)
        if model.get("schema") != model_engine.SCHEMA or model_engine.is_legacy(model):
            frappe.throw(_("'model' must be a Fountain Designer model (schema {0}).").format(model_engine.SCHEMA))
        sent = list(tables) + list(global_inputs)
        model = copy.deepcopy(model)
        ignored = [f"{k} (a model was sent, and it is the whole design)" for k in sent]
        stored_acks = [a for a in (existing or {}).get("acks") or [] if isinstance(a, dict)]
        if model.get("acks") is None and stored_acks:
            # A model read back without its sign-offs must not erase them: the stored ones are kept. An
            # explicit list, even an empty one, is the model's own.
            model["acks"] = copy.deepcopy(stored_acks)
            ignored.append(f"acks (the model sent had none, so the design's {len(stored_acks)} "
                           "stored sign-off(s) were kept)")
        return model, ignored
    if existing and model_engine.is_legacy(existing):
        # A design never converted: its stored schema-1 model is the only copy, so it is converted
        # first and the payload laid on it, never replaced by a model built from the payload alone.
        acks = existing.get("acks") or []
        existing = model_engine.migrate(existing)
        existing["acks"] = acks
    if existing:
        if not tables and not global_inputs:
            return copy.deepcopy(existing), []
        return merge_legacy_payload(existing, tables, global_inputs)
    legacy = dict(tables)
    legacy["global_inputs"] = global_inputs
    legacy.setdefault("basins", [])
    migrated = model_engine.migrate(legacy)
    migrated["acks"] = []
    ignored = [f"{t} (the Fountain Designer derives these)" for t in DERIVED_TABLES if t in tables]
    ignored += [k for k in global_inputs if k not in _GLOBAL_INPUT_PATHS]
    return migrated, ignored


def tool_save(name=None, model=None, tables=None, global_inputs=None, fields=None):
    """Create (no ``name``) or update a design for the ``save_water_design`` AI tool, through
    ``save_design``, the page's own save (its permission checks, its re-derive, its refusal of an issued
    design). Returns ``(design name, ignored)``."""
    existing = None
    if name:
        if not frappe.db.exists(DESIGN_DOCTYPE, name):
            frappe.throw(_("Water Feature Design {0} was not found.").format(name), frappe.DoesNotExistError)
        existing = stored_model(frappe.get_doc(DESIGN_DOCTYPE, name))
    built, ignored = tool_model(existing, model, tables, global_inputs)
    fields = fields or {}
    record = {k: v for k, v in fields.items() if k in DESIGN_FIELDS}
    ignored += [f"{k} (set it on the record in ERPNext)" for k in fields if k not in DESIGN_FIELDS]
    saved = save_design(name or None, json.dumps(built), json.dumps(record))
    return saved["name"], ignored


def designer_url(name, step="review"):
    """The Fountain Designer open on one design, at a step (``router.js``: ``?design=&step=``)."""
    return f"/desk/{DESIGNER_ROUTE}?" + urlencode({"design": name, "step": step})


def paper_links(name, prints=None):
    """``[{key, label, print_format, url}]``: the print view of each paper this site has, the packet
    last, as the Outputs step links them (``steps/outputs.js`` ``print_url``)."""
    rows = print_formats() if prints is None else prints
    return [
        dict(r, url="/printview?" + urlencode(
            {"doctype": DESIGN_DOCTYPE, "name": name, "format": r["print_format"], "no_letterhead": 1}))
        for r in rows
    ]


def _num(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _json_value(raw, kind):
    if isinstance(raw, kind):
        return raw
    try:
        data = json.loads(raw or ("[]" if kind is list else "{}"))
    except (TypeError, ValueError):
        return kind()
    return data if isinstance(data, kind) else kind()


# Worst first: the canvas colours the supply line by the worst segment (fountain_canvas.statusColor).
_VELOCITY_ORDER = (pipe_engine.STATUS_EXCEEDS, pipe_engine.STATUS_INCREASE, pipe_engine.STATUS_SETTLING,
                   pipe_engine.STATUS_OKAY)


def segment_velocity_status(segment, value):
    """A segment's velocity band (``engine.pipe.velocity_status``) from its derived velocity, its line
    and its pipe's limits (``data/pipe_specs``); "" when the velocity or the pipe is not known."""
    velocity = (value or {}).get("velocity_fps")
    if velocity in (None, ""):
        return ""
    # A material the spec table does not know has no limits to band against: no band, not PVC's.
    spec = (PIPE_SPECS.get(segment.get("material") or "SCH40 PVC") or {}).get(
        (value or {}).get("size") or segment.get("nominal_size") or "")
    if not spec:
        return ""
    return pipe_engine.velocity_status(_num(velocity), segment.get("line") or "discharge", spec["max_suction_fps"],
                                       spec["max_discharge_fps"], spec["legal_fps"])


def canvas_state(doc, model=None, stored=None):
    """The fountain-design "canvas" the shared SVG renderer draws (``window.WaterFountain``, and the
    Triton chat from ``water_design_status``), from the schema-2 model and the last derive's per-row
    values; ``model`` and ``stored`` (``results_json``) when the caller has already parsed them.
    (``api/water_design._canvas_state`` until v1.771.0.)"""

    def _ft(inch):
        return f"{round(_num(inch) / 12.0, 1):g}"

    model = (stored_model(doc) or {}) if model is None else model
    seg_values = (((stored_json(doc) if stored is None else stored).get("values") or {}).get("segments") or {})
    network = model.get("network") or {}

    label = ""
    basins = model.get("basins") or []
    if basins:
        b = basins[0]
        if str(b.get("shape") or "").lower().startswith(("round", "cyl", "circ")) and _num(b.get("diameter_in")):
            label = f"{_ft(b.get('diameter_in'))} ft dia x {_ft(b.get('depth_in'))} ft deep"
        elif _num(b.get("length_in")) and _num(b.get("width_in")):
            label = f"{_ft(b.get('length_in'))} x {_ft(b.get('width_in'))} ft x {_ft(b.get('depth_in'))} ft deep"

    features = model.get("features") or []
    jet = max((_num((f.get("inputs") or {}).get("height_ft")) for f in features), default=0.0)
    tiers = [
        {"diameter_in": _num(t.get("diameter_in")), "rim_height_in": _num(t.get("rim_height_in"))}
        for f in features
        for t in (f.get("inputs") or {}).get("tiers") or []
    ]
    pump = doc.get("selected_pump")
    segments = [
        {"label": s.get("label") or s.get("id"), "velocity_fps": (seg_values.get(s.get("id")) or {}).get("velocity_fps"),
         "velocity_status": segment_velocity_status(s, seg_values.get(s.get("id")))}
        for s in network.get("segments") or []
    ]
    statuses = {s["velocity_status"] for s in segments if s["velocity_status"]}
    return {
        "basin_gallons": doc.get("total_basin_gallons"),
        "flow_gpm": doc.get("design_flow_gpm"),
        "feature_count": len(features),
        "feature_kind": feature_visual_kind(features[0].get("type") or "") if features else None,
        "tiers": tiers,
        "jet_height_ft": jet or None,
        "pump": pump,
        "tdh_ft": doc.get("computed_tdh_ft"),
        "static_lift_ft": max((_num(n.get("elevation_ft")) for n in network.get("nodes") or []
                               if n.get("kind") == "terminal"), default=0.0),
        "basin_label": label,
        "worst_status": next((st for st in _VELOCITY_ORDER if st in statuses), ""),
        "segments": segments,
        "curve": (pump_curves([pump]).get(pump) if pump else None) or [],
        "duty_flow": _num(doc.get("operating_flow_gpm")) or _num(doc.get("design_flow_gpm")),
        "duty_head": _num(doc.get("computed_tdh_ft")),
    }


def design_status(name, include_results=True, include_model=False):
    """One design as the page shows it, for the ``water_design_status`` AI tool: the verdict and the
    rollups of the last derive (``results_json``), the typed issues (``design_issues_json``), the
    readiness (``readiness_json``) and counts, the Fountain Designer link and every paper's print link;
    with ``include_model``, the schema-2 model itself. Checks read permission on the record."""
    doc = frappe.get_doc(DESIGN_DOCTYPE, name)
    doc.check_permission("read")
    stored = stored_json(doc)
    model = stored_model(doc) or {}
    network = model.get("network") or {}
    issues = _json_value(doc.get("design_issues_json"), list)
    readiness = _json_value(doc.get("readiness_json"), dict)
    papers = paper_links(doc.name)
    state = {
        "design": doc.name,
        "design_title": doc.get("design_title"),
        "status": doc.get("status"),
        "docstatus": doc.docstatus,
        "mode": doc.get("mode"),
        "kit": doc.get("kit"),
        "family": doc.get("family"),
        "project": doc.get("project"),
        "opportunity": doc.get("opportunity"),
        "customer": doc.get("customer"),
        "schema": model.get("schema"),
        # What the page's right panel shows: the stored verdict, else the one these issues give.
        "verdict": stored.get("verdict") or issues_mod.model_verdict(issues, readiness),
        "rollups": stored.get("rollups") or {},
        "budget_total": doc.get("budget_total"),
        "bom_subtotal": doc.get("bom_subtotal"),
        "issues": issues,
        "blocker_count": doc.get("blocker_count") or 0,
        "warning_count": doc.get("warning_count") or 0,
        "issue_summary": doc.get("issue_summary") or "",
        "readiness": readiness,
        "completion_percent": doc.get("completion_percent") or 0,
        "issue_ready": bool(doc.get("issue_ready")),
        "next_inputs_needed": [s for s in (doc.get("next_inputs_needed") or "").split("\n") if s],
        "counts": {
            "basins": len(model.get("basins") or []),
            "features": len(model.get("features") or []),
            "pipe_segments": len(network.get("segments") or []),
            "pumps": len(network.get("pumps") or []),
            "bom_lines": len(doc.get("bom") or []),
        },
        "fitting_schedule": issues_mod.fitting_schedule(doc),
        "canvas": canvas_state(doc, model, stored),
        "designer_url": designer_url(doc.name),
        "form_url": f"/desk/{_slug(DESIGN_DOCTYPE)}/{doc.name}",
        "prints": [p for p in papers if p["key"] != "packet"],
        "packet": next((p for p in papers if p["key"] == "packet"), None),
    }
    if include_model:
        state["model"] = model
    if include_results:
        state["calc_results"] = [
            {
                "calc": key,
                "value": r.get("value"),
                "unit": r.get("unit"),
                "formula": r.get("formula"),
                "steps": "\n".join(str(s) for s in r.get("steps") or []),
                "citations": ", ".join(str(c) for c in r.get("citations") or []),
                "warnings": "\n".join(str(w) for w in r.get("warnings") or []),
            }
            for key, r in (stored.get("results") or {}).items()
            if isinstance(r, dict)
        ]
    return state


def design_list(project=None, limit=20):
    """The designs the caller may read, newest first, each with its counts and its designer link."""
    rows = frappe.get_list(
        DESIGN_DOCTYPE,
        filters={"project": project} if project else {},
        fields=["name", "design_title", "project", "opportunity", "customer", "status", "mode", "kit",
                "completion_percent", "blocker_count", "warning_count", "issue_ready", "budget_total", "modified"],
        order_by="modified desc",
        limit_page_length=limit,
    )
    for r in rows:
        r["designer_url"] = designer_url(r["name"])
    return {"designs": rows}
