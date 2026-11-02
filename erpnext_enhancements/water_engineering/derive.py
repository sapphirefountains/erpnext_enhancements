"""The Fountain Designer's derive: one design model in, the whole automation ladder out.

``derive_model(model, catalogs, rules)`` runs ``docs/fountain-designer/PLAN.md`` section 2.2 in
order and returns the reply the page draws (``public/js/fountain_designer/README.md``, "The
contract", ``derive``): the derived model, every ``CalcResult``, the per-row values, the rollups,
the typed issues, readiness, the verdict, the BOM, the charts and the outputs.

The ladder, and the engine each step calls:

* 0 site: blank site fields from the address lookups the caller passes (``site_lookup``: the point,
  the elevation and the USGS seismic values), then the state's row of the jurisdiction table and the
  house-rule utility rates; ``regulated`` from the state's rules (pools, spas, interactive features);
  and the ``jurisdiction`` result (code editions, agency, regulated venues, licensing) the prints read.
* 1 basins: ``basin.basin_volume``.
* 2 features: ``engine.families`` (flow, checks, terminal) per feature.
* 3 regulated minimums: ``aquatic.turnover_time`` / ``minimum_flow_rate`` on a regulated venue.
* 5-8 basin and surge, routes and pipe sizes, the network solve and the pump, control, bypass and
  surge: ``engine.autosize.derive`` (phase B), after the route template here fills the lengths and
  fittings of an ``auto`` piping section from the Pump room step.
* 9 treatment: ``treatment.filtration_area`` and ``upkeep.water_plan``.
* 10 lighting and electrical: the lighting system module, ``electrical_report``, ``controls_report``.
* 10b structure: ``structural_takeoff``.
* 11 winterization: the winterization system module.
* 12 outputs: ``bom.bom_rows`` merged with the electrical, controls and structural lines and priced
  from the caller's ``prices`` (labor at the house-rule rate); ``upkeep.om_per_year`` and
  ``maintenance_schedule``; the duty, schematic and one-line SVGs; and ``paper``, what the print
  formats read (``water_engineering/prints.py``, phase G).

Nothing here imports frappe, so the whole ladder runs bench-free (``tests/test_designer_derive.py``).
The frappe side (catalogs from phase D's ``catalog_reader``, Item prices, the house rules on Water
Engineering Settings, the geocoder) lives in ``api/designer.py`` and hands plain dicts in.

Two promises the page relies on: a ``custom`` section is never written (``autosize.derive`` and the
steps here write ``auto`` sections only), and a half-drawn model never raises. Every stage runs
guarded; a stage that fails becomes one ``ENGINE_STAGE_FAILED`` blocker naming it, and the stages
after it still run on what there is. Every float in the reply is finite or None (strict JSON).
"""

from __future__ import annotations

import copy
import json
import math
import zlib
from collections.abc import Callable
from typing import Any

from . import issues as design_issues
from . import prints
from .engine import autosize, drawings, kits, upkeep
from .engine import bom as bom_engine
from .engine import jurisdiction as juris
from .engine import model as model_engine
from .engine import rules as rules_engine
from .engine.aquatic import minimum_flow_rate
from .engine.basin import basin_volume
from .engine.constants import FT_PER_PSI
from .engine.controls import controls_report
from .engine.electrical import electrical_report
from .engine.families import SYSTEM_MODULES, run_feature
from .engine.structural import structural_takeoff
from .engine.treatment import evaporation_rate, filtration_area
from .engine.units import gallons_to_pounds

# The site fields the engine fills where the designer left them blank (phase J): from the address
# lookups (the geocoded point, Google's elevation, USGS's seismic design values), then the state's row
# of the jurisdiction table (``engine/data/jurisdictions.json``: the design city's climate), then the
# house-rule utility rates.
SITE_LOOKUPS = (
    "lat", "lng", "elevation_ft", "air_temp_f", "water_temp_f", "frost_depth_in", "hardness_ppm", "kwh_rate",
    "water_rate_per_ccf", "seismic_sdc", "sds", "sd1", "winterize",
)
# The ones the jurisdiction table gives, by the name its climate block uses.
CLIMATE_KEYS = {"elevation_ft": "elevation_ft", "air_temp_f": "air_temp_f", "water_temp_f": "water_temp_f",
                "frost_depth_in": "frost_depth_in", "hardness_ppm": "hardness_ppm", "seismic_sdc": "seismic_default",
                "winterize": "winterize"}
CLIMATE_LABELS = {"elevation_ft": "elevation", "air_temp_f": "design air temperature", "water_temp_f": "design water temperature",
                  "frost_depth_in": "frost depth", "hardness_ppm": "water hardness", "seismic_sdc": "seismic design category",
                  "winterize": "whether it freezes"}
SITE_UNITS = {"lat": "deg", "lng": "deg", "elevation_ft": "ft", "air_temp_f": "F", "water_temp_f": "F", "frost_depth_in": "in",
              "hardness_ppm": "ppm", "kwh_rate": "$/kWh", "water_rate_per_ccf": "$/ccf", "seismic_sdc": "", "sds": "g",
              "sd1": "g", "winterize": ""}
# ASCE 7 categories, and the IRC's D0 to D2 the table's schema allows (catalog/jurisdictions-validate.py).
SDC_VALUES = ("A", "B", "C", "D", "D0", "D1", "D2", "E", "F")
CIT_HOUSE_RATES = "Water Engineering Settings house rules"
# The house-rule utility rates are sourced for Salt Lake City (``engine/rules.py``: SLC water, Rocky
# Mountain Power), so a site in another state is told to type its own.
HOUSE_RATES_STATE = "UT"

# The Pump room step's route template (PLAN.md 2.2 step 6), applied to an ``auto`` piping section
# where a segment has no length yet. Sapphire standard 2026-11-01, for the designers to confirm.
CIT_ROUTE = "Sapphire standard, 2026-11-01 (route template: confirm with the designers)"
ROUTE_FITTINGS = {
    "suction": [{"key": "entrance", "qty": 1}, {"key": "elbow_90", "qty": 2}, {"key": "ball", "qty": 1}],
    "discharge": [{"key": "elbow_90", "qty": 4}, {"key": "check", "qty": 1}, {"key": "ball", "qty": 1},
                  {"key": "exit", "qty": 1}],
}
PUMP_ELEVATION_FT = {"vault": -3.0, "room": 0.0, "submersible": -1.0}
SUCTION_FT = {"vault": 10.0, "submersible": 2.0}

# House-rule hours per day the circulation runs, when the site gives none.
DEFAULT_HOURS_PER_DAY = 12.0
DEFAULT_HW_C = 130
HW_CROSSCHECK_PCT = 10.0

# The outputs the page lists (key, label, Print Format) live in prints.OUTPUTS beside the formats.
SCHEDULES = tuple((key, label) for key, label, _pf in prints.OUTPUTS)


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """A finite float, or None for blanks, junk, NaN and infinities."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def finite(x: Any) -> Any:
    """The tree with every non-finite float as None, so ``json.dumps(..., allow_nan=False)`` holds."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [finite(v) for v in x]
    return x


def _as_dict(result: Any) -> dict:
    if result is None:
        return {}
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return dict(result) if isinstance(result, dict) else {}


def _round(x: Any, digits: int = 2) -> float | None:
    v = _num(x)
    return None if v is None else round(v, digits)


def _hash(text: str) -> str:
    return f"{zlib.crc32(str(text).encode('utf-8')) & 0xFFFFFFFF:08x}"


class _Run:
    """The state one derive builds up: results, checks and the stage failures."""

    def __init__(self, on_error: Callable[[str, BaseException], None] | None):
        self.results: dict[str, dict] = {}
        self.checks: list[dict] = []
        self.failures: list[dict] = []
        self.on_error = on_error

    def result(self, r: Any, name: str | None = None) -> dict:
        d = _as_dict(r)
        if not d:
            return d
        key = name or d.get("calc") or "calc"
        base, n = key, 2
        while key in self.results:
            key = f"{base}#{n}"
            n += 1
        self.results[key] = d
        return d

    def stage(self, name: str, fn: Callable[[], Any], default: Any = None) -> Any:
        try:
            return fn()
        except Exception as exc:  # a half-drawn model must never fail the whole derive
            self.failures.append({"stage": name, "error": f"{type(exc).__name__}: {exc}"[:300]})
            if self.on_error:
                try:
                    self.on_error(name, exc)
                except Exception:
                    pass
            return default


# --------------------------------------------------------------------------------------
# step 0: site
# --------------------------------------------------------------------------------------


# The venues a health department regulates are an allow-list (pool, spa, interactive), read from the
# venue's words in ``regulated_site``. ``aquatic.is_regulated`` is a deny-list of fountain venues
# (anything else counts as a pool), so a pond, a lake or an event rental came out regulated and got a
# spa's code-minimum blocker.


def regulated_site(site: dict) -> bool:
    """Regulated when the venue is a public pool, a spa the state regulates (``regulation.spa_regulated``),
    or an interactive water feature in a state that regulates them (``regulation.iwf_regulated``). The
    state's row of the jurisdiction table decides, never the code "UT"; a state with no row regulates
    pools and spas as everywhere, and interactive features not at all (the interactive family warns).
    Worked out from the venue every time, never read back from ``site.regulated``: the derive writes that
    field, so reading it would keep a design regulated after its venue changed."""
    words = str(site.get("venue_type") or "").lower().replace("/", " ").split()
    entry = juris.entry_for(site)
    if "pool" in words:
        return True
    if "spa" in words:
        return juris.spa_regulated(entry)
    return "interactive" in words and juris.iwf_regulated(entry)


def _site_value(key: str, value: Any) -> Any:
    """A site value in its kind (a number, the SDC letter, the winterize flag), or None when blank or junk."""
    if key == "winterize":
        return value if isinstance(value, bool) else None
    if key == "seismic_sdc":
        letter = str(value or "").strip().upper()
        return letter if letter in SDC_VALUES else None
    return _num(value)


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{value:g}" if isinstance(value, float) else str(value)


def engine_owned(site: dict, key: str) -> str | None:
    """Who filled ``site[key]`` (``lookup``, ``default``, ``house_rule``, ``rule``), or None when it is the
    designer's. ``site.sources[key]`` is ``{"src", "v"}``, the value the engine wrote; a field holding any
    other value was changed since by someone (the page, the ``save_water_design`` tool, a script, an older
    client), so it is theirs. A blanked field with a source is the engine's to fill again."""
    sources = site.get("sources") if isinstance(site.get("sources"), dict) else {}
    rec = sources.get(key)
    if key not in SITE_LOOKUPS or not isinstance(rec, dict) or not rec.get("src"):
        return None
    have = _site_value(key, site.get(key))
    if have is None or have == _site_value(key, rec.get("v")):
        return str(rec["src"])
    return None


def derive_site(site: dict, rules: dict, lookup: dict | None = None) -> tuple[dict, dict]:
    """Fill the site fields the designer did not type: the caller's address lookups first (the geocoded
    point, the elevation, the USGS seismic values), then the state's row of the jurisdiction table, then
    the house-rule utility rates. Returns (site, CalcResult dict naming the source of each value).

    ``site.sources`` records which values the engine filled and what it wrote (``{key: {"src", "v"}}``,
    :func:`engine_owned`): a value with no source, or one changed since the engine wrote it, is the
    designer's and is never touched; one the engine still owns is refreshed, so a new state's climate
    replaces the old state's, and a lookup made on a save replaces a state default. A looked-up value
    stays until a new lookup answers (the page's own derive makes none). An engine value whose source no
    longer has one (a state with no frost depth on file) is blanked rather than left standing from the
    old state. ``winterize`` follows a frost depth the designer typed (``rule``): 0 or less means the site
    does not freeze, whatever the state's design city does."""
    out = dict(site or {})
    lookup = dict(lookup or {})
    lookup_cites = lookup.pop("cites", None) or {}
    owned = {k: src for k in SITE_LOOKUPS if (src := engine_owned(out, k))}
    entry = juris.entry_for(out)
    state = juris.state_name(out, entry)
    climate = juris.climate(entry)
    climate_cites = climate.get("cites") if isinstance(climate.get("cites"), dict) else {}
    house = {"kwh_rate": rules.get("kwh_rate"), "water_rate_per_ccf": rules.get("water_rate_per_ccf")}
    shown: dict[str, dict] = {}
    citations: list[str] = []

    def cite(text: Any) -> str:
        text = str(text or "")
        if text and text not in citations:
            citations.append(text)
        return text

    for key in SITE_LOOKUPS:
        have = _site_value(key, out.get(key))
        owner = owned.get(key)
        if have is not None and owner is None:
            continue  # the designer's
        fresh = _site_value(key, lookup.get(key))
        if fresh is None and owner == "lookup" and have is not None:
            shown[key] = {"value": have, "unit": SITE_UNITS.get(key, ""), "source": "lookup",
                          "ref": cite(lookup_cites.get(key) or "address lookup (an earlier save)")}
            continue
        candidates = (
            ("lookup", fresh, lookup_cites.get(key) or "address lookup"),
            ("default", _site_value(key, climate.get(CLIMATE_KEYS[key])) if key in CLIMATE_KEYS else None,
             climate_cites.get(CLIMATE_KEYS.get(key, key)) or f"{state} jurisdiction defaults"),
            ("house_rule", _site_value(key, house.get(key)), CIT_HOUSE_RATES),
        )
        pick = next(((src, v, ref) for src, v, ref in candidates if v is not None), None)
        if pick:
            src, v, ref = pick
            out[key] = v
            owned[key] = src
            shown[key] = {"value": v, "unit": SITE_UNITS.get(key, ""), "source": src, "ref": cite(ref)}
        elif owner:
            out[key] = None
            owned.pop(key, None)
    # A frost depth the designer typed decides whether the site freezes, unless they ticked Winterize too.
    frost = _site_value("frost_depth_in", out.get("frost_depth_in"))
    if frost is not None and "frost_depth_in" not in owned and \
            (owned.get("winterize") or _site_value("winterize", out.get("winterize")) is None):
        out["winterize"] = frost > 0
        owned["winterize"] = "rule"
        shown["winterize"] = {"value": out["winterize"], "unit": "", "source": "rule",
                              "ref": cite("the frost depth typed for the site (0 or less: it does not freeze)")}
    if owned:
        out["sources"] = {k: {"src": src, "v": out.get(k)} for k, src in owned.items()}
    else:
        out.pop("sources", None)
    out["regulated"] = regulated_site(out)
    warnings = []
    house_rated = [k for k in ("kwh_rate", "water_rate_per_ccf") if owned.get(k) == "house_rule"]
    if house_rated and juris.code_of(out) != HOUSE_RATES_STATE:
        warnings.append("The power and water rates are the house rules, sourced for Salt Lake City: type the "
                        f"site's own utility rates for {state}, or the running costs are Utah's.")
    if entry is None:
        warnings.append(juris.no_data_warning(out))
    else:
        blank = [CLIMATE_LABELS[k] for k in CLIMATE_KEYS if _site_value(k, out.get(k)) is None]
        if blank:
            warnings.append(f"{state} has no {', '.join(blank)} on file for the design city: type "
                            f"{'it' if len(blank) == 1 else 'them'} on the Site step.")
    steps = [f"{k} = {_show(meta['value'])} ({meta['source']}: {meta['ref']})" for k, meta in shown.items()]
    return out, {
        "calc": "site_lookups", "value": len(shown), "unit": "fields", "inputs": shown,
        "formula": "blank site fields <- address lookups, then the state's jurisdiction defaults, then house rules",
        "steps": steps or ["every site field was given"], "citations": citations,
        "warnings": warnings, "options": [], "status": "Okay" if not warnings else "Check",
    }


def jurisdiction_checks(result: dict | None) -> list[dict]:
    """One issue for the codes the designer must confirm with the authority having jurisdiction: a
    warning when the state has no row, or adopts a code locally or not at all (to sign off once it is
    confirmed); an advisory when the table only lacks an edition."""
    v = (result or {}).get("value") or {}
    if not result:
        return []
    if not v.get("known"):
        return [{"code": "JURISDICTION_NO_DATA", "key": "JURISDICTION_NO_DATA", "severity": "warning", "section": "site",
                 "title": f"No jurisdiction data for {v.get('code') or 'this site'}",
                 "detail": " ".join(result.get("warnings") or []),
                 "remedy": "Pick the site's state, or type the site facts and confirm the codes with the authority having jurisdiction.",
                 "_source": "engine"}]
    if not v.get("confirm_ahj"):
        return []
    return [{"code": "JURISDICTION_CONFIRM_AHJ", "key": "JURISDICTION_CONFIRM_AHJ",
             "severity": "warning" if v.get("ahj_local") else "info", "section": "site",
             "title": f"Confirm the code editions for {v.get('name') or v.get('code')}", "detail": v.get("ahj_note") or "",
             "remedy": "Call the building department and the health department, then sign this off.",
             "citation": "; ".join((result.get("citations") or [])[:3]), "_source": "engine"}]


# --------------------------------------------------------------------------------------
# steps 1-2: basins and features
# --------------------------------------------------------------------------------------


def _basin_volume(b: dict) -> dict:
    override = _num(b.get("volume_gal_override"))
    shape = str(b.get("shape") or "rectangular").lower()
    if override:
        return {"calc": "basin_volume", "value": override, "unit": "gal", "inputs": {}, "formula": "override",
                "steps": [f"published volume {override:g} gal"], "citations": [], "warnings": [], "options": [],
                "status": "Okay"}
    if shape.startswith(("round", "cyl", "circ")):
        r = basin_volume("Cylindrical", diameter_in=_num(b.get("diameter_in")) or 0.0, height_in=_num(b.get("depth_in")) or 0.0)
        return _as_dict(r)
    area = _num(b.get("surface_area_sqft"))
    if shape.startswith(("other", "irreg", "free")) and area:
        gal = area * (_num(b.get("depth_in")) or 0.0) / 12.0 * 7.480519
        return {"calc": "basin_volume", "value": gal, "unit": "gal", "inputs": {}, "formula": "area x depth x 7.4805",
                "steps": [f"{area:g} sq ft x {(_num(b.get('depth_in')) or 0) / 12:.2f} ft x 7.4805 = {gal:.1f} gal"],
                "citations": [], "warnings": [], "options": [], "status": "Okay"}
    r = basin_volume("Rectangular", length_in=_num(b.get("length_in")) or 0.0, width_in=_num(b.get("width_in")) or 0.0,
                     height_in=_num(b.get("depth_in")) or 0.0)
    return _as_dict(r)


def _family_ctx(feature: dict, basins_by_id: dict, volumes: dict) -> dict:
    basin = basins_by_id.get(feature.get("basin"))
    ctx: dict[str, Any] = {"basin": basin}
    if basin:
        gal = volumes.get(basin.get("id"))
        if feature.get("family") == "weir":
            ctx["trough_volume_gal"] = gal
        elif feature.get("family") == "interactive":
            ctx["tank_gal"] = gal
    return ctx


_TAKEOFF_BASIS = ("k", "cd", "rated_gpm")
# A takeoff the family gives a flow but no head for (an interactive ground jet): the pressure it is
# rated at until its nozzle is picked. Sapphire standard 2026-11-01, for the designers to confirm.
TAKEOFF_HEAD_FT = 15.0


def _terminal_params(feature: dict, flow: dict, terminal: dict | None) -> dict:
    """A family's terminal params in the form phase B's ``terminals.build_demand`` and
    ``manifold.manifold_from_us`` read. The families write ``design_flow_gpm`` and, for a jet
    manifold, each takeoff's design ``flow_gpm`` at ``head_ft``; phase B reads ``design_gpm`` and
    a takeoff's ``k``, ``cd`` + orifice size, or ``rated_gpm`` + ``rated_head_ft``."""
    params = dict((terminal or {}).get("params") or {})
    gpm = _num((flow or {}).get("data", {}).get("gpm")) or _num((flow or {}).get("value"))
    if gpm:
        params["design_gpm"] = gpm
    takeoffs = []
    for t in params.get("takeoffs") or []:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        if not any(_num(t.get(k)) for k in _TAKEOFF_BASIS) and _num(t.get("flow_gpm")):
            head = _num(t.get("head_ft")) or _num(params.get("head_ft")) or TAKEOFF_HEAD_FT
            if head == TAKEOFF_HEAD_FT and not _num(t.get("head_ft")):
                params["head_assumed_ft"] = TAKEOFF_HEAD_FT
            t["rated_gpm"], t["rated_head_ft"] = _num(t["flow_gpm"]), head
        takeoffs.append(t)
    if takeoffs:
        params["takeoffs"] = takeoffs
    if (terminal or {}).get("kind") == "orifice" and not (_num(params.get("cd")) or _num(params.get("rated_gpm"))):
        head = _num(params.get("head_ft")) or _num(params.get("residual_head_ft"))
        q = _num(params.get("flow_gpm")) or gpm
        if q and head:
            params["rated_gpm"], params["rated_head_ft"] = q, head
    return params


# --------------------------------------------------------------------------------------
# step 6: the route template
# --------------------------------------------------------------------------------------


def _pumped(feature: dict) -> bool:
    return kits._pumped(feature)


def _add_routes(m: dict) -> bool:
    """An ``auto`` piping section whose network has pumps and terminals but no segments gets the
    kit's routes: the basin to each pump, each pump to every terminal. A pump with no basin to draw
    from (a pondless reservoir) gets a source node of its own."""
    net = m.get("network") or {}
    if net.get("segments") or (m.get("setup") or {}).get("piping", "auto") != "auto":
        return False
    pumps = [p.get("id") for p in net.get("pumps") or [] if p.get("id")]
    terms = [t for t in net.get("terminals") or [] if t.get("id")]
    if not pumps or not terms:
        return False
    nodes = net.setdefault("nodes", [])
    ids = {n.get("id") for n in nodes}
    src = next((n.get("id") for n in nodes if n.get("kind") == "basin"), None)
    if src is None:
        src = "src"
        nodes.insert(0, {"id": src, "kind": "basin", "basin": (m.get("basins") or [{}])[0].get("id"), "elevation_ft": 0.0})
    segs = net.setdefault("segments", [])
    for pid in pumps:
        if pid not in ids:
            nodes.append({"id": pid, "kind": "pump"})
        segs.append({"id": f"s{len(segs) + 1}", "from": src, "to": pid, "line": "suction", "material": "SCH40 PVC",
                     "nominal_size": None, "length_ft": 0.0, "fittings": [], "components": []})
    for t in terms:
        node = t.get("node") or t.get("id")
        if node not in ids:
            nodes.append({"id": node, "kind": "terminal", "feature": t.get("feature"), "elevation_ft": 0.0})
            ids.add(node)
        segs.append({"id": f"s{len(segs) + 1}", "from": pumps[0], "to": node, "line": "discharge", "material": "SCH40 PVC",
                     "nominal_size": None, "length_ft": 0.0, "fittings": [], "components": []})
    return True


def _next_id(taken: set, prefix: str) -> str:
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _sync_terminals(m: dict) -> bool:
    """An ``auto`` piping section follows the features: a pumped feature added since the last derive
    gets a terminal node and a discharge run from the first pump, a removed one loses its terminal,
    node and runs, and every terminal takes its family's kind and lift afresh (the fall height may
    have changed). A ``custom`` network is never touched."""
    net = m.get("network") or {}
    pumps = [p.get("id") for p in net.get("pumps") or [] if p.get("id")]
    if not pumps:
        return False
    features = {f.get("id"): f for f in m.get("features") or [] if isinstance(f, dict) and f.get("id")}
    pumped = [fid for fid, f in features.items() if _pumped(f)]
    nodes = net.setdefault("nodes", [])
    terms = net.setdefault("terminals", [])
    segs = net.setdefault("segments", [])
    changed = False
    gone = {t.get("node") or t.get("id") for t in terms if t.get("feature") and t.get("feature") not in pumped}
    if gone:
        terms[:] = [t for t in terms if (t.get("node") or t.get("id")) not in gone]
        nodes[:] = [n for n in nodes if not (n.get("kind") == "terminal" and n.get("id") in gone)]
        segs[:] = [s for s in segs if s.get("to") not in gone and s.get("from") not in gone]
        changed = True
    by_feature = {t.get("feature"): t for t in terms if t.get("feature")}
    node_ids = {n.get("id") for n in nodes}
    for fid in pumped:
        f = features[fid]
        family_term = f.get("_terminal") or {}
        lift = _num((family_term.get("params") or {}).get("lift_ft"))
        term = by_feature.get(fid)
        if term is None:
            node_id = f.get("node") if f.get("node") and f.get("node") not in node_ids else _next_id(node_ids, "t")
            node_ids.add(node_id)
            f["node"] = node_id
            nodes.append({"id": node_id, "kind": "terminal", "feature": fid, "elevation_ft": lift or 0.0})
            terms.append({"id": node_id, "kind": family_term.get("kind") or "open_discharge", "feature": fid, "params": {}})
            segs.append({"id": _next_id({s.get("id") for s in segs}, "s"), "from": pumps[0], "to": node_id,
                         "line": "discharge", "material": "SCH40 PVC", "nominal_size": None, "length_ft": 0.0,
                         "fittings": [], "components": []})
            changed = True
            continue
        if family_term.get("kind"):
            term["kind"] = family_term["kind"]
        node = next((n for n in nodes if n.get("id") == (term.get("node") or term.get("id"))), None)
        if node is not None and lift is not None:
            node["elevation_ft"] = lift
    return changed


def build_network(m: dict) -> bool:
    """Lay the kit skeleton (basin -> pump -> each pumped feature) into an empty network; in an
    ``auto`` piping section, keep an existing network in step with the features and lay routes it
    lacks. Returns True when it changed anything. A ``custom`` network is the person's and stays."""
    net = m.get("network") or {}
    if net.get("nodes"):
        if (m.get("setup") or {}).get("piping", "auto") != "auto":
            return False
        built = _add_routes(m)
        built = _sync_terminals(m) or built
        if not m.get("basins"):
            built = _add_source(net) or built
        return built
    if not any(_pumped(f) for f in m.get("features") or []):
        return False
    room = m.get("pump_room") or {}
    klass = next((p.get("generic_class") for p in net.get("pumps") or [] if p.get("generic_class")), None)
    pump = {"generic_class": klass or ("submersible" if room.get("location") == "submersible" else "end-suction"),
            "location": room.get("location") or "vault"}
    features = [dict(f, node=f.get("node") or f"t{i + 1}") for i, f in enumerate(m.get("features") or [])]
    for f, g in zip(m.get("features") or [], features, strict=False):
        if _pumped(f) and not f.get("node"):
            f["node"] = g["node"]
    m["network"] = kits._network([f for f in features if f.get("id")], m.get("basins") or [], pump)
    if not m.get("basins"):
        _add_source(m["network"])
    return True


def _add_source(net: dict) -> bool:
    """A pumped feature with no basin (a pondless reservoir, a vault tank) still draws from water:
    a source node and a suction segment, so the network can be solved."""
    if any(n.get("kind") == "basin" for n in net.get("nodes") or []):
        return False
    pumps = [p.get("id") for p in net.get("pumps") or []]
    if not pumps:
        return False
    net.setdefault("nodes", []).insert(0, {"id": "src", "kind": "basin", "basin": None, "elevation_ft": 0.0})
    segs = net.setdefault("segments", [])
    for pid in pumps:
        if not any(s.get("to") == pid for s in segs):
            segs.insert(0, {"id": f"s-{pid}", "from": "src", "to": pid, "line": "suction", "material": "SCH40 PVC",
                            "nominal_size": None, "length_ft": 0.0, "fittings": [], "components": []})
    return True


def apply_route_template(m: dict) -> dict | None:
    """Lay every segment of an ``auto`` piping section from the Pump room step: suction from the
    basin to the pump, discharge the pump-to-feature distance plus the rise, the step's material,
    the template's fittings, and no size, so phase B sizes it for today's flows. In an ``auto``
    section the engine owns every one of these, so they are laid afresh on each derive: values an
    earlier derive wrote never pin it (phase B sizes only a segment with no size). Returns the
    CalcResult, or None for a ``custom`` section."""
    if (m.get("setup") or {}).get("piping", "auto") != "auto":
        return None
    room = m.get("pump_room") or {}
    net = m.get("network") or {}
    location = str(room.get("location") or next((p.get("location") for p in net.get("pumps") or [] if p.get("location")), None)
                   or "vault")
    distance = _num(room.get("distance_ft"))
    material = room.get("material") or None
    nodes = {n.get("id"): n for n in net.get("nodes") or []}
    pumps = {p.get("id") for p in net.get("pumps") or []}
    pump_elev = _num(room.get("elevation_ft"))
    if pump_elev is None:
        pump_elev = PUMP_ELEVATION_FT.get(location, 0.0)
    for pid in pumps:
        if nodes.get(pid) is not None:
            nodes[pid]["elevation_ft"] = pump_elev
    steps = []
    for s in net.get("segments") or []:
        line = s.get("line") or ("suction" if s.get("to") in pumps else "discharge")
        if line == "suction":
            length = distance if location == "room" and distance else SUCTION_FT.get(location, 10.0)
        else:
            rise = (_num((nodes.get(s.get("to")) or {}).get("elevation_ft")) or 0.0) - pump_elev
            length = (distance if distance is not None else 25.0) + max(rise, 0.0)
        s["length_ft"] = round(length, 1)
        if material:
            s["material"] = material
        s["fittings"] = copy.deepcopy(ROUTE_FITTINGS["suction" if line == "suction" else "discharge"])
        s["nominal_size"] = None
        s.pop("id_in", None)
        steps.append(f"{s.get('id')}: {line} {s['length_ft']:g} ft, {s.get('material')}")
    if not steps:
        return None
    return {"calc": "route_template", "value": len(steps), "unit": "segments", "inputs": {
        "location": {"value": location, "unit": "", "source": "user", "ref": "pump_room.location"},
        "distance_ft": {"value": distance, "unit": "ft", "source": "user" if distance is not None else "default",
                        "ref": "pump_room.distance_ft"}},
        "formula": "suction: vault 10 ft, submersible 2 ft, room = distance ; discharge = distance + rise",
        "steps": steps, "citations": [CIT_ROUTE], "warnings": [] if distance is not None else
        ["No pump-to-feature distance: the discharge assumes 25 ft. Enter it on the Pump room step."],
        "options": [], "status": "Okay"}


# --------------------------------------------------------------------------------------
# the Hazen-Williams cross-check (PLAN.md 1.8)
# --------------------------------------------------------------------------------------


def hw_crosscheck(network_result: dict | None, segments: list | None = None, c: float = DEFAULT_HW_C) -> dict | None:
    """The DOC-0049 Hazen-Williams friction beside the governing Darcy figure, over every solved
    segment's pipe friction. Phase A found C=130 runs 15-35% above Darcy on PVC, so the spread is
    an advisory, not a warning a designer must sign off on every design. The solve reports no
    lengths, so they come from the model's ``segments``."""
    if not network_result:
        return None
    from .engine.pipe import hazen_williams_loss

    lengths = {s.get("id"): _num(s.get("length_ft")) for s in segments or [] if isinstance(s, dict)}
    darcy = hw = 0.0
    steps = []
    for sid, s in (network_result.get("segments") or {}).items():
        branches = max(int(_num(s.get("branches")) or 1), 1)
        q, d, f = _num(s.get("flow_gpm")), _num(s.get("id_in")), _num(s.get("friction_factor"))
        v, length = _num(s.get("velocity_fps")), lengths.get(sid)
        if not (q and d and f and v and length):
            continue
        q = q / branches
        major = f * (length / (d / 12.0)) * v * v / (2 * 32.174)
        h = _num(_as_dict(hazen_williams_loss(q, length, d, c)).get("value")) or 0.0
        darcy += major
        hw += h
        steps.append(f"{sid}: Darcy {major:.2f} ft, Hazen-Williams {h:.2f} ft")
    if darcy <= 0:
        return None
    pct = (hw - darcy) / darcy * 100.0
    return {"calc": "hazen_williams_crosscheck", "value": round(pct, 1), "unit": "%", "inputs": {
        "darcy_ft": {"value": darcy, "unit": "ft", "source": "prior_calc", "ref": "network solve"},
        "hazen_williams_ft": {"value": hw, "unit": "ft", "source": "prior_calc", "ref": f"C={c:g}"}},
        "formula": "(HW - Darcy) / Darcy over the pipe friction of every solved segment", "steps": steps,
        "citations": ["DOC-0049 (Hazen-Williams C=130, the workbook figure); Darcy-Weisbach governs (PLAN.md 1.8)"],
        "warnings": [], "options": [], "status": "Diverges" if abs(pct) > HW_CROSSCHECK_PCT else "Okay"}


# --------------------------------------------------------------------------------------
# step 12: prices and the BOM
# --------------------------------------------------------------------------------------


def _line_item(line: dict, model: dict, hints: dict) -> tuple[str | None, str]:
    """(Item, catalog source) for a BOM line from its hint: the Item phase B names, the picked pump,
    or a text hint or part number the caller's ``prices.hints`` maps to an Item."""
    hint = line.get("item_hint") or {}
    item = hint.get("item")
    if hint.get("kind") == "pump":
        pump = next((p for p in (model.get("network") or {}).get("pumps") or [] if p.get("id") == hint.get("pump")), {})
        item = item or pump.get("item")
        return item, str(pump.get("catalog_source") or ("" if item else "generic estimate"))
    for key in ("part_no", "hint"):
        if not item and hint.get(key):
            item = hints.get(str(hint[key]))
    return item, ""


def _hint_dict(line: dict) -> dict:
    """Phase B's line has a dict ``item_hint``; phases L and S send a text hint (and a ``part_no``).
    ``bom.merge_sections`` keeps only a dict, so fold both into one before merging."""
    hint = line.get("item_hint")
    out = dict(hint) if isinstance(hint, dict) else ({"hint": str(hint)} if hint else {})
    if line.get("part_no"):
        out["part_no"] = str(line["part_no"])
    return dict(line, item_hint=out)


def price_bom(lines: list[dict], model: dict, prices: dict | None, rules: dict | None = None) -> list[dict]:
    """The merged engine lines as Water Feature Design Line rows (the contract's ``bom[]``).

    A labor line (hours, no Item) is priced at the house rule ``labor_rate_per_hr``; 0 is "not set",
    so the line stays unpriced and the Budget print says so (Nikolas, 2026-11-01: no fabricated labor)."""
    items = (prices or {}).get("items") or {}
    hints = (prices or {}).get("hints") or {}
    labor_rate = rules_engine.rule(rules, "labor_rate_per_hr")
    out = []
    for ln in lines:
        item, source = _line_item(ln, model, hints)
        qty = _num(ln.get("qty")) or 0.0
        info = items.get(item) or {} if item else {}
        rate = _num(info.get("rate")) or 0.0
        if item and not source:
            source = str(info.get("catalog_source") or "")
        if not item and ln.get("section") == "Labor" and labor_rate > 0:
            rate, source = labor_rate, "house rule"
        out.append({
            "section": ln.get("section") or "",
            "item": item or None,
            "description": ln.get("description") or "",
            "qty": round(qty, 3),
            "uom": ln.get("uom") or "ea",
            "rate": round(rate, 2),
            # on the quantity the line prints and pushes, so qty x rate on paper is the amount to the cent
            "amount": round(round(rate, 2) * round(qty, 3), 2),
            "source": "Auto",
            "catalog_source": source,
            "line_key": str(ln.get("key") or "")[:250],
        })
    return out


def merge_bom(hydraulic: list, electrical: dict | None, controls: dict | None, structural: dict | None,
              families: list) -> list[dict]:
    """``bom.merge_sections`` over every phase's lines. The controls 24 V DC supply covers the
    electrical valve supply, so the electrical ``lvpsu:valves`` line goes when controls has one
    (phase L's hand-over)."""
    elec = list((electrical or {}).get("bom") or [])
    ctl = list((controls or {}).get("bom") or [])
    if any(str(x.get("key") or "").startswith("psu:") for x in ctl):
        elec = [x for x in elec if x.get("key") != "lvpsu:valves"]
    lists = (hydraulic or [], families or [], elec, ctl, list((structural or {}).get("bom") or []))
    return bom_engine.merge_sections(*[[_hint_dict(x) for x in lst if isinstance(x, dict)] for lst in lists])


# --------------------------------------------------------------------------------------
# the derive
# --------------------------------------------------------------------------------------


def _catalog_pumps(catalogs: dict | None) -> list[dict]:
    return list((catalogs or {}).get("pumps") or [])


def resolve_curves(m: dict, catalogs: dict | None) -> list[dict]:
    """A pump whose ``curve`` names a catalog curve (a Pump Curve name, or an Item) gets that curve's
    points, the shape phase B solves. Returns a check for each name the catalog does not have."""
    rows = _catalog_pumps(catalogs)
    checks = []
    for p in (m.get("network") or {}).get("pumps") or []:
        ref = p.get("curve")
        if not isinstance(ref, str):
            continue
        row = next((r for r in rows if ref in (r.get("curve_name"), r.get("name"))), None) or \
            next((r for r in rows if r.get("item") == ref), None)
        if row is None:
            p["curve"] = None
            checks.append({"code": "PUMP_CURVE_NOT_FOUND", "key": f"PUMP_CURVE_NOT_FOUND|{p.get('id')}", "severity": "warning",
                           "section": "pump", "ref": p.get("id"), "title": f"Pump {p.get('id')}: curve {ref} is not in the catalog",
                           "remedy": "Pick the pump again, or add its Pump Curve.", "_source": "engine"})
            continue
        p["curve"] = {"points": row.get("points") or [], **{k: row.get(k) for k in ("shutoff_head_ft", "max_flow_gpm", "motor_eff_pct")}}
        for k in ("hp", "motor_eff_pct", "catalog_source"):
            if row.get(k) is not None and p.get(k) is None:
                p[k] = row[k]
    return checks


def pond_area(m: dict) -> None:
    """An irregular basin with no plan area takes it from the pond acres an aerator in it gives
    (43,560 sq ft an acre), when the basin section is ``auto``."""
    auto = (m.get("setup") or {}).get("basin", "auto") == "auto"
    for b in m.get("basins") or []:
        if auto and not (_num(b.get("surface_area_sqft")) or _num(b.get("diameter_in"))
                         or (_num(b.get("length_in")) and _num(b.get("width_in")))):
            for f in m.get("features") or []:
                acres = _num((f.get("inputs") or {}).get("surface_acres")) or _num((f.get("inputs") or {}).get("pond_acres"))
                if f.get("basin") == b.get("id") and acres:
                    b["surface_area_sqft"] = round(acres * 43560.0, 1)
                    break
        # phase C calls a freeform plan area surface_area_sqft; phase B's basin checks read area_sf
        if _num(b.get("surface_area_sqft")) and not _num(b.get("area_sf")):
            b["area_sf"] = _num(b["surface_area_sqft"])


def _pumps_for_power(m: dict, network_result: dict | None, ranking: list) -> list[dict]:
    """Pump power for phase L's reports: the catalog HP of the pump picked now, else the operating
    kW. The catalog figure comes first: an ``hp`` an earlier derive stored belongs to whichever pump
    was picked then, and phase B changes the item and curve on a re-pick but not the HP."""
    by_item = {r.get("item"): r for r in ranking or [] if r.get("item")}
    ops = (network_result or {}).get("pumps") or {}
    auto = (m.get("setup") or {}).get("pump", "auto") == "auto"
    out = []
    for p in (m.get("network") or {}).get("pumps") or []:
        op = ops.get(p.get("id")) or {}
        if not auto:  # the person's pump: their HP or kW stands; the operating kW only fills a blank
            if not (_num(p.get("hp")) or _num(p.get("kw") or p.get("fla"))) and _num(op.get("electrical_kw")):
                p["kw"] = round(_num(op["electrical_kw"]), 3)
            out.append(p)
            continue
        picked = by_item.get(p.get("item"))
        hp = _num(picked.get("hp")) if picked else _num(p.get("hp"))
        kw = _num(op.get("electrical_kw"))
        p.pop("kw", None)
        if hp:
            p["hp"] = hp
        else:
            p.pop("hp", None)
            if kw:
                p["kw"] = round(kw, 3)
        out.append(p)
    return out


def _aerator_power(m: dict, flows: dict, rules: dict) -> None:
    """A floating aerator's motor for phase L's loads, as ``feature["_power"]`` (dropped again before the
    reply): the unit HP typed from its Item (``unit_hp``) else the family's HP-per-acre pick split over its
    units, its cable run, and the house-rule running hours (``aerator_duty_hours_day``, 24 by default)."""
    hours = rules_engine.rule(rules, "aerator_duty_hours_day")
    for f in m.get("features") or []:
        if f.get("family") != "aerator":
            continue
        data = (flows.get(f.get("id")) or {}).get("data") or {}
        units = int(_num(data.get("units")) or 1)
        unit_hp = _num((f.get("inputs") or {}).get("unit_hp")) or _num(data.get("hp_per_unit"))
        if unit_hp:
            f["_power"] = {"hp": round(unit_hp, 3), "units": units, "cable_ft": _num(data.get("cable_run_ft")) or 0.0,
                           "duty_hours_day": hours}


def _lighting_fixtures(m: dict, light: dict) -> None:
    """An ``auto`` lighting section with no fixtures takes one row per lit effect from the house
    rules, so the electrical step has loads to size."""
    if (m.get("setup") or {}).get("lighting", "auto") != "auto":
        return
    lighting = m.setdefault("lighting", {})
    if lighting.get("fixtures"):
        return
    rows = []
    for eff in (light.get("data") or {}).get("effects") or []:
        n = int(_num(eff.get("fixtures")) or 0)
        if n <= 0:
            continue
        watts = (_num(eff.get("watts")) or 0.0) / n
        rows.append({"feature": eff.get("feature"), "qty": n, "watts": round(watts, 1), "voltage": 12,
                     "beam": eff.get("beam"), "auto": True})
    lighting["fixtures"] = rows


def _feature_values(m: dict, flows: dict, network_result: dict | None) -> dict:
    terms = (network_result or {}).get("terminals") or {}
    by_feature = {}
    for t in (m.get("network") or {}).get("terminals") or []:
        if t.get("feature"):
            by_feature[t["feature"]] = terms.get(t.get("id")) or {}
    out = {}
    for f in m.get("features") or []:
        fl = flows.get(f.get("id")) or {}
        term = by_feature.get(f.get("id")) or {}
        psi = _num(term.get("pressure_psi"))
        head = psi * FT_PER_PSI if psi is not None else None
        data = fl.get("data") or {}
        if head is None and _num(data.get("head_in")) is not None:
            head = _num(data.get("head_in")) / 12.0
        steps = fl.get("steps") or []
        out[f.get("id")] = {"flow_gpm": _round(fl.get("value"), 1), "head_ft": _round(head, 2),
                            "note": (steps[0] if steps else fl.get("formula") or "")[:160] or None}
    return out


def _basin_values(m: dict, volumes: dict, basin_checks: dict) -> dict:
    out = {}
    for b in m.get("basins") or []:
        gal = volumes.get(b.get("id"))
        bc = basin_checks.get(b.get("id")) or {}
        out[b.get("id")] = {"volume_gal": _round(gal, 1), "weight_lb": _round(gallons_to_pounds(gal), 0) if gal else None,
                            "surge_in": _round(bc.get("level_rise_in"), 2), "freeboard_in": _round(bc.get("freeboard_in"), 2)}
    return out


def _segment_values(network_result: dict | None, m: dict) -> dict:
    segs = (network_result or {}).get("segments") or {}
    out = {}
    for s in (m.get("network") or {}).get("segments") or []:
        r = segs.get(s.get("id")) or {}
        out[s.get("id")] = {"velocity_fps": _round(r.get("velocity_fps"), 2), "loss_ft": _round(r.get("loss_ft"), 2),
                            "size": r.get("nominal_size") or s.get("nominal_size")}
    return out


def _pump_values(network_result: dict | None, m: dict) -> dict:
    ops = (network_result or {}).get("pumps") or {}
    out = {}
    for p in (m.get("network") or {}).get("pumps") or []:
        r = ops.get(p.get("id")) or {}
        ratio = _num(r.get("bep_ratio"))
        out[p.get("id")] = {"flow_gpm": _round(r.get("flow_gpm"), 1), "head_ft": _round(r.get("head_ft"), 1),
                            "kw": _round(r.get("electrical_kw"), 2), "npsh_margin_ft": _round(r.get("npsh_margin_ft"), 1),
                            "bep_pct": _round(ratio * 100.0, 0) if ratio is not None else None}
    return out


def _pump_label(p: dict, ranking: list) -> str:
    if p.get("label"):
        return str(p["label"])
    row = next((r for r in ranking or [] if r.get("item") and r.get("item") == p.get("item")), None)
    if row and row.get("name"):
        return str(row["name"])
    if p.get("item"):
        return str(p["item"])
    klass = p.get("generic_class") or "pump"
    return f"{str(klass).replace('-', ' ').capitalize()} (generic)"


def _duty_state(m: dict, network_result: dict | None, design_gpm: float | None, rules: dict) -> dict | None:
    """The input of ``drawings.pump_duty_svg`` for the first running pump."""
    if not network_result:
        return None
    ops = network_result.get("pumps") or {}
    pumps = (m.get("network") or {}).get("pumps") or []
    p = next((x for x in pumps if ops.get(x.get("id"))), None)
    if p is None:
        return None
    op = ops[p["id"]]
    raw = p.get("curve")
    points = raw.get("points") if isinstance(raw, dict) else raw
    curve = [{"flow_gpm": _num(r.get("flow_gpm")), "head_ft": _num(r.get("head_ft"))} for r in points or []
             if isinstance(r, dict) and _num(r.get("flow_gpm")) is not None and _num(r.get("head_ft")) is not None]
    if not curve and isinstance(raw, dict) and _num(raw.get("shutoff_head_ft")) and _num(raw.get("max_flow_gpm")):
        curve = [{"flow_gpm": 0.0, "head_ft": _num(raw["shutoff_head_ft"])}, {"flow_gpm": _num(raw["max_flow_gpm"]), "head_ft": 0.0}]
    speed = _num(op.get("speed_pct"))
    speeds = [round(speed, 0)] if speed and speed < 99.5 else []
    q, h = _num(op.get("flow_gpm")), _num(op.get("head_ft"))
    # The system curve through the operating point: static head plus friction rising with Q^2.
    system = []
    static = None
    src = [n for n in (m.get("network") or {}).get("nodes") or [] if n.get("kind") == "basin"]
    terms = [n for n in (m.get("network") or {}).get("nodes") or [] if n.get("kind") == "terminal"]
    if src and terms:
        static = max((_num(t.get("elevation_ft")) or 0.0) for t in terms) - (_num(src[0].get("elevation_ft")) or 0.0)
    if q and h is not None:
        s0 = max(static or 0.0, 0.0)
        k = max(h - s0, 0.0) / (q * q) if q else 0.0
        top = max(q * 1.4, (design_gpm or 0) * 1.2)
        system = [{"flow_gpm": round(top * i / 12, 2), "head_ft": round(s0 + k * (top * i / 12) ** 2, 2)} for i in range(13)]
    bep = _num(op.get("bep_gpm"))  # phase B reports it at the operating speed
    lo, hi = float(rules.get("bep_min") or 0.7), float(rules.get("bep_max") or 1.2)
    return {
        "title": "Duty point",
        "pumps": [{"label": _pump_label(p, []), "curve": curve, "speeds": speeds}],
        "system": system,
        "duty": {"flow_gpm": q, "head_ft": h},
        "bep": {"flow_lo_gpm": bep * lo, "flow_hi_gpm": bep * hi} if bep else None,
        "npsh": {"available_ft": _num(op.get("npsha_ft")), "required_ft": _num(op.get("npshr_ft")),
                 "margin_ft": _num(op.get("npsh_margin_ft"))},
        "design": {"flow_gpm": design_gpm} if design_gpm else None,
    }


PUMP_ROLES = {"vault": "in a vault", "room": "in the pump room", "submersible": "submersible"}


def _schematic_state(m: dict) -> dict:
    """Each box: a name on one line and a short role under it. Models and ratings are the schedules';
    a pump's full name ran past its box."""
    nodes = []
    basins = m.get("basins") or []
    if basins:
        nodes.append({"label": basins[0].get("label") or "Basin", "sub": "source"})
    room = (m.get("pump_room") or {}).get("location") or "vault"
    nodes.append({"label": "Pump", "sub": PUMP_ROLES.get(room, room)})
    slots = [e.get("slot") for e in m.get("equipment") or []]
    if "filter" in slots or (m.get("treatment") or {}).get("sanitizer") not in (None, "", "none"):
        nodes.append({"label": "Filter", "sub": "treatment"})
    if (m.get("treatment") or {}).get("uv"):
        nodes.append({"label": "UV", "sub": "secondary"})
    feats = m.get("features") or []
    if feats:
        nodes.append({"label": feats[0].get("label") or feats[0].get("type") or "Feature",
                      "sub": f"+{len(feats) - 1} more" if len(feats) > 1 else feats[0].get("family") or ""})
    return {"title": "Circulation", "nodes": nodes}


def _page_svg(svg: Any) -> str | None:
    if not svg:
        return None
    adapt = getattr(drawings, "page_svg", None)
    return adapt(svg) if adapt else svg


def _startup(m: dict, regulated: bool) -> list[str]:
    steps = ["Fill the basin to the operating level and check the autofill holds it."]
    room = (m.get("pump_room") or {}).get("location")
    if room != "submersible":
        steps.append("Prime the pump, open the suction and discharge valves, and check the rotation.")
    steps.append("Run the pump and read the gauges: compare the flow and the head with the duty point.")
    families = {f.get("family") for f in m.get("features") or []}
    if "weir" in families:
        steps.append("Level the weir lip to the edge tolerance before the first full run.")
    if families & {"jets", "foam", "laminar", "interactive"}:
        steps.append("Set each nozzle's height and trim the valves until the display matches the design.")
    if (m.get("lighting") or {}).get("fixtures"):
        steps.append("Test the GFCI on every lighting and pump circuit (NEC 680).")
    steps.append("Balance the water chemistry before the feature opens.")
    if regulated:
        steps.append("Book the health department's inspection with the plan-check sheet.")
    return steps


# What results_json keeps of phase L's reports: the page's keys (E) and what the print formats read (G).
ELECTRICAL_KEYS = ("panel_rows", "control_pump_rows", "checklist", "energy", "circuits", "feeder", "service", "edition",
                   "state_edition", "code_cite", "verify", "inputs", "low_voltage", "transformers")
CONTROLS_KEYS = ("control_panel", "theory_of_operation", "bom", "panel", "controller", "pumps", "relays", "dc_supply")
GAL_PER_CCF = upkeep.GAL_PER_CCF
DAYS_PER_YEAR = 365.0


def budget_rules(rules: dict) -> dict:
    """The budget's house rules as the prints and the record read them (``prints.budget_summary``)."""
    return {
        "labor_rate_per_hr": rules_engine.rule(rules, "labor_rate_per_hr"),
        "contingency_pct": rules_engine.rule(rules, "budget_contingency_pct"),
        "range_pct": rules_engine.rule(rules, "budget_range_pct"),
    }


def _maintenance(m: dict, plan: dict, bom: list, ctl: dict | None, hours: float, *, outdoor: bool, frost: bool) -> list[dict]:
    """``upkeep.maintenance_schedule`` for the design as derived: its filters, UV, lights, drives, nozzles
    and switched valves."""
    filters = sum(int(_num(e.get("qty")) or 1) for e in m.get("equipment") or [] if e.get("slot") == "filter")
    lights = sum(int(_num(x.get("qty")) or 1) for x in (m.get("lighting") or {}).get("fixtures") or [] if isinstance(x, dict))
    nozzles = int(sum(_num(r.get("qty")) or 0 for r in bom or [] if r.get("section") == "Nozzles"))
    valves = int(_num((((ctl or {}).get("control_panel") or {}).get("parent") or {}).get("solenoid_valve_qty")) or 0)
    vfd = any(str(p.get("control") or "").lower() == "vfd" for p in (m.get("network") or {}).get("pumps") or [])
    return upkeep.maintenance_schedule(plan, outdoor=outdoor, frost=frost, filters=filters, uv=bool((m.get("treatment") or {}).get("uv")),
                                       lights=lights, vfd=vfd, nozzles=nozzles, switched_valves=valves,
                                       run_hours_year=hours * DAYS_PER_YEAR)


def _input_value(inputs: dict, key: str) -> float | None:
    entry = inputs.get(key)
    return _num(entry.get("value")) if isinstance(entry, dict) else _num(entry)


def water_lines(plan: dict, om: dict, om_inputs: dict, total_gal: float) -> list[dict]:
    """``om.water`` split by where the water goes: evaporation, spray and splash, and the drain-and-refills.
    The three add up to ``om_per_year``'s own water figure (same days, same rate); the filter's backwash
    is not in that figure yet, so it is listed with no number rather than a guess."""
    days = _input_value(om_inputs, "days") or DAYS_PER_YEAR
    rate = _input_value(om_inputs, "water_rate") or 0.0
    refills = _num(om.get("refills")) or 0.0
    rows = [
        ("evaporation", "Evaporation", (_num(plan.get("evaporation_gpd")) or 0.0) * days, ""),
        ("spray", "Spray and splash loss", (_num(plan.get("spray_gpd")) or 0.0) * days, ""),
        ("refill", "Drain and refill", refills * (total_gal or 0.0),
         "Never: a pond or lake is not drained" if plan.get("natural") else
         f"{refills:.1f} a year, every {plan.get('refresh_days')} days ({plan.get('refresh_by') or 'hygiene'})" if refills else ""),
    ]
    out = [{"key": k, "label": label, "gal": round(gal, 1), "cost": round(gal / GAL_PER_CCF * rate, 2), "note": note}
           for k, label, gal, note in rows]
    out.append({"key": "backwash", "label": "Filter backwash", "gal": None, "cost": None,
                "note": "Not in the engine's water figure yet: add it for a sand or DE filter."})
    return out


def _checklist_text(winterize: list, spring: list, startup: list) -> str:
    """The checklists as plain text with a box per step, to paste into a Maintenance Visit or a schedule."""
    lines = []
    if winterize:
        lines += ["WINTERIZE (before the first freeze)"] + [f"[ ] {i.get('title')}" for i in winterize if i.get("title")]
    if spring:
        lines += ([""] if lines else []) + ["SPRING START-UP"] + [f"[ ] {i.get('title')}" for i in spring if i.get("title")]
    if startup:
        lines += ([""] if lines else []) + ["FIRST START-UP CHECKS"] + [f"[ ] {s}" for s in startup]
    return "\n".join(lines)


def paper_data(m: dict, *, plan: dict | None, om: dict | None, om_inputs: dict, maintenance: list, wint: dict, frost: bool,
               iwf: list, elec: dict | None, rules: dict, regulated: bool, total_gal: float, startup: list) -> dict:
    """What the print formats read beyond the page's reply (phase G, ``prints.py``): the O&M detail, the
    winterization checklist with its drain points, the plan-check lines (passes included) and the budget
    rules. Engine output reshaped, never recomputed."""
    segs = (m.get("network") or {}).get("segments") or []
    frost_in = _num(wint.get("frost_depth_in")) or _num((m.get("site") or {}).get("frost_depth_in"))
    winterize = [i for i in wint.get("winterize") or [] if isinstance(i, dict)] if frost else []
    spring = [i for i in wint.get("spring_startup") or [] if isinstance(i, dict)] if frost else []
    return {
        "upkeep": {
            "water_plan": {k: v for k, v in (plan or {}).items() if k != "checks"} if plan else None,
            "om": om,
            "water_lines": water_lines(plan, om, om_inputs, total_gal) if plan and om else [],
            "rates": {"kwh_rate": _input_value(om_inputs, "kwh_rate"), "water_rate_per_ccf": _input_value(om_inputs, "water_rate"),
                      "chlorine_per_gal": _input_value(om_inputs, "chlorine_price"), "days": _input_value(om_inputs, "days")},
            "maintenance": maintenance or [],
        },
        "winterization": {
            "needed": frost,
            "frost_depth_in": frost_in,
            "equipment_room": (m.get("pump_room") or {}).get("location"),
            "drain_points": [s.get("id") for s in segs if s.get("drain") or s.get("slopes_to_drain")],
            "below_frost": [s.get("id") for s in segs if frost_in and (_num(s.get("buried_depth_in")) or 0) >= frost_in],
            "winterize": winterize,
            "spring_startup": spring,
            "checklist_text": _checklist_text(winterize, spring, startup),
        },
        "plan_check": {
            "regulated": regulated,
            "iwf": [{k: c.get(k) for k in ("feature", "feature_label", "code", "title", "severity", "detail", "remedy",
                                           "citation", "value", "limit", "unit")} for c in iwf],
            "nec680": list((elec or {}).get("checklist") or []),
            "nec_edition": (elec or {}).get("edition"),
            "nec_state_edition": (elec or {}).get("state_edition"),
            "nec_verify": (elec or {}).get("verify"),
        },
        "budget_rules": budget_rules(rules),
    }


def derive_model(model: dict | str, catalogs: dict | None = None, rules: dict | None = None, *,
                 site_lookup: dict | None = None, prices: dict | None = None,
                 on_error: Callable[[str, BaseException], None] | None = None) -> dict:
    """Run the ladder over a schema-2 model. See the module docstring for the steps.

    ``catalogs``: ``{"pumps": [phase B catalog rows]}`` (``api/designer.load_catalogs`` maps phase D's
    Pump Curves onto them). ``rules``: the house rules (``engine.rules.resolve``). ``site_lookup``: the
    address lookup (``{lat, lng, elevation_ft, ...}``) for the blank site fields. ``prices``:
    ``{"items": {item: {rate, catalog_source}}, "hints": {hint: item}}``. ``on_error(stage, exc)`` is
    called for a stage that failed (the API logs it).
    """
    run = _Run(on_error)
    rules = dict(rules if rules is not None else rules_engine.resolve({}))
    if isinstance(model, str):
        try:
            model = json.loads(model or "{}")
        except ValueError:
            model = {}
    src = model if isinstance(model, dict) else {}
    m = model_engine.Model.from_dict(copy.deepcopy(src)).to_dict()
    problems = [p.to_dict() for p in model_engine.validate(m)]
    setup = m.get("setup") or {}

    # 0 site
    site, site_result = run.stage("site", lambda: derive_site(m.get("site") or {}, rules, site_lookup),
                                  (m.get("site") or {}, None))
    m["site"] = site
    run.result(site_result)
    jr = run.result(run.stage("jurisdiction", lambda: juris.summary(site)))
    run.checks.extend(run.stage("jurisdiction checks", lambda: jurisdiction_checks(jr), []))
    regulated = bool(site.get("regulated"))

    # 1 basins (measured again after phase B, which deepens an auto basin: see below)
    run.stage("pond area", lambda: pond_area(m))

    def measure() -> dict[str, float]:
        out: dict[str, float] = {}
        for b in m.get("basins") or []:
            r = run.stage(f"basin {b.get('id')}", lambda b=b: _basin_volume(b), {})
            run.results[f"basin_volume|{b.get('id')}"] = r  # the latest measure replaces the first
            if _num(r.get("value")):
                out[b.get("id")] = _num(r["value"])
        return out

    volumes = measure()
    total_gal = sum(volumes.values())

    # 2 features
    basins_by_id = {b.get("id"): b for b in m.get("basins") or []}
    flows: dict[str, dict] = {}
    family_lines: list[dict] = []
    iwf_lines: list[dict] = []
    for f in m.get("features") or []:
        out = run.stage(f"feature {f.get('id')}",
                        lambda f=f: run_feature(f, site, rules, _family_ctx(f, basins_by_id, volumes)), None)
        if not out:
            continue
        flows[f.get("id")] = out.get("flow") or {}
        run.result(out.get("flow"), f"{(out.get('flow') or {}).get('calc') or f.get('family')}|{f.get('id')}")
        for c in out.get("checks") or []:
            run.checks.append({**c, "_source": "family", "feature": c.get("feature") or f.get("id")})
            if str(c.get("code") or "").startswith("IWF_"):
                iwf_lines.append({**c, "feature": c.get("feature") or f.get("id"), "feature_label": f.get("label") or f.get("id")})
        for ln in (out.get("flow") or {}).get("data", {}).get("bom") or []:
            if isinstance(ln, dict):
                family_lines.append(ln)
        f["_terminal"] = out.get("terminal")
    design_gpm = sum((_num((fl.get("data") or {}).get("gpm")) or _num(fl.get("value")) or 0.0)
                     for fl in flows.values()) or None

    # 5-8 network: skeleton, terminals, route template, then phase B
    run.stage("network skeleton", lambda: build_network(m), False)
    net = m.setdefault("network", {"nodes": [], "pumps": [], "segments": [], "terminals": []})
    feats_by_id = {f.get("id"): f for f in m.get("features") or []}
    for t in net.get("terminals") or []:
        f = feats_by_id.get(t.get("feature"))
        if f is not None:
            t["params"] = _terminal_params(f, flows.get(f.get("id")) or {}, {
                "kind": t.get("kind"), "params": {**((f.get("_terminal") or {}).get("params") or {}), **(t.get("params") or {})}})
    for f in m.get("features") or []:
        f.pop("_terminal", None)
    run.result(run.stage("route template", lambda: apply_route_template(m)))
    run.checks.extend(run.stage("pump curves", lambda: resolve_curves(m, catalogs), []))
    ab = run.stage("network", lambda: autosize.derive(m, setup, {"pumps": _catalog_pumps(catalogs)}, rules), None) \
        if net.get("pumps") else None
    network_result = None
    ranking: list = []
    basin_checks: dict[str, dict] = {}
    if ab:
        derived = ab.get("model") or m
        # phase B writes auto sections only and keeps everything else as it came
        m = derived
        basin_ids = [b.get("id") for b in m.get("basins") or []]
        for r in ab.get("results") or []:
            d = run.result(r)
            # phase B appends one basin_checks result per basin, in the model's basin order
            if d.get("calc") == "basin_checks" and isinstance(d.get("value"), dict) and len(basin_checks) < len(basin_ids):
                basin_checks[basin_ids[len(basin_checks)]] = d["value"]
        for c in ab.get("checks") or []:
            run.checks.append({**c, "_source": "network"})
        network_result = ab.get("network")
        ranking = ab.get("ranking") or []
        sources = {r.get("item"): r.get("catalog_source") for r in _catalog_pumps(catalogs) if r.get("item")}
        for p in (m.get("network") or {}).get("pumps") or []:
            if p.get("item") and sources.get(p["item"]):
                p["catalog_source"] = sources[p["item"]]
    # 1 again: phase B sizes an auto basin's depth, so the volume every later step and the rollups read
    # is measured on the basins as they now stand, not as they came in (one reply, one set of numbers).
    volumes = measure()
    total_gal = sum(volumes.values())
    op_gpm = _num(((network_result or {}).get("totals") or {}).get("pump_flow_gpm"))

    # 3 regulated minimums, at the flow that actually circulates (Nikolas, 2026-11-01: checks run at the
    # operating flow); the design flow stands in only when there is no solved network.
    venue = str(site.get("venue_type") or "")
    if regulated and total_gal and "interactive" not in venue.lower():
        r = run.stage("minimum flow", lambda: minimum_flow_rate(total_gal, 0.0, "pool" if "pool" in venue.lower() else "spa",
                                                                   site.get("governing_code") or ""))
        mf = run.result(r)
        need = _num(mf.get("value"))
        have = op_gpm if op_gpm else design_gpm
        if need and have is not None and have < need:
            basis = "operating" if op_gpm else "design"
            run.checks.append({"code": "REG_MIN_FLOW", "severity": "blocker", "section": "pump",
                               "title": f"Circulation {have:.0f} gpm ({basis}) is under the code minimum {need:.0f} gpm",
                               "remedy": "Raise the circulation flow (a larger pump or a separate filter loop).",
                               "citation": "; ".join(mf.get("citations") or []), "_source": "engine"})

    hw = run.stage("hazen-williams", lambda: hw_crosscheck(network_result, (m.get("network") or {}).get("segments")))
    if hw:
        run.result(hw)
        if hw.get("status") == "Diverges":
            run.checks.append({"code": "HW_CROSSCHECK", "key": "HW_CROSSCHECK", "severity": "info", "section": "piping",
                               "title": f"Hazen-Williams reads {hw['value']:+.0f}% against the governing Darcy friction",
                               "remedy": "Darcy-Weisbach governs (PLAN.md 1.8); the DOC-0049 figure is shown as the workbook cross-check.",
                               "citation": hw["citations"][0], "_source": "engine"})

    # 9 treatment
    circ = max(op_gpm or 0.0, design_gpm or 0.0) or None
    if circ and (m.get("treatment") or {}).get("sanitizer") not in (None, "", "none"):
        media = (m.get("treatment") or {}).get("filter_media") or "cartridge"
        run.result(run.stage("filtration", lambda: filtration_area(circ, media)))
    evap = sum(_num(v.get("evaporation_gph")) or 0.0 for v in basin_checks.values())
    spray = sum(_num(v.get("spray_loss_gph")) or 0.0 for v in basin_checks.values())
    hours = _num(site.get("hours_per_day")) or DEFAULT_HOURS_PER_DAY
    indoor = any((f.get("inputs") or {}).get("indoor") for f in m.get("features") or [])
    plan = None
    if total_gal:
        natural = upkeep.natural_water(site.get("venue_type"), [f.get("family") for f in m.get("features") or []])
        if natural:
            # A pond or lake evaporates all day from its whole surface, pump or no pump (an aerator pond has no
            # network, so phase B's basin checks never ran). ASHRAE over the plan area, per day; the water plan
            # multiplies its evaporation by the pump hours, so it is handed as the day's figure over those hours.
            area = sum(_num(b.get("area_sf")) or _num(b.get("surface_area_sqft")) or 0.0 for b in m.get("basins") or [])
            if area:
                er = run.result(run.stage("evaporation", lambda: evaporation_rate(
                    area, _num(site.get("water_temp_f")) or 68.0, _num(site.get("air_temp_f")) or 85.0,
                    _num(site.get("rh_pct")) or 40.0)))
                evap = (_num(er.get("value")) or 0.0) / hours
        wp = run.stage("water plan", lambda: upkeep.water_plan(
            total_gal, evaporation_gph=evap, spray_gph=spray, hours_per_day=hours,
            water_temp_f=_num(site.get("water_temp_f")) or 68.0, outdoor=not indoor,
            treatment=str((m.get("treatment") or {}).get("sanitizer") or "chlorine"), natural=natural))
        plan = run.result(wp).get("value") if wp else None
        for c in (plan or {}).get("checks") or []:
            run.checks.append({**c, "_source": "network"})

    # 10 lighting and electrical. The fixtures an earlier derive wrote (``auto``) are dropped first,
    # so the house rules size them afresh and the lighting checks see the same source every time.
    if (m.get("setup") or {}).get("lighting", "auto") == "auto":
        lighting = m.setdefault("lighting", {})
        lighting["fixtures"] = [x for x in lighting.get("fixtures") or [] if not (isinstance(x, dict) and x.get("auto"))]
    light_ctx = {"flows": {k: (v.get("data") or {}) for k, v in flows.items()}}
    light = run.stage("lighting", lambda: _as_dict(SYSTEM_MODULES["lighting"].calc(m, light_ctx, rules)), {})
    run.result(light)
    for c in run.stage("lighting checks", lambda: [x.to_dict() for x in SYSTEM_MODULES["lighting"].checks(
            m, dict(light_ctx, lighting=light.get("data")), rules)], []):
        run.checks.append({**c, "_source": "family"})
    run.stage("lighting fixtures", lambda: _lighting_fixtures(m, light))
    run.stage("pump power", lambda: _pumps_for_power(m, network_result, ranking))
    run.stage("aerator power", lambda: _aerator_power(m, flows, rules))
    elec = run.stage("electrical", lambda: electrical_report(m), None)
    ctl = run.stage("controls", lambda: controls_report(m, electrical=elec), None) if elec else None
    for f in m.get("features") or []:
        f.pop("_power", None)
    for name, rep in (("electrical", elec), ("controls", ctl)):
        for r in (rep or {}).get("results") or []:
            run.result(r)
        notes = [str(w) for w in (rep or {}).get("warnings") or [] if w]
        if notes:
            # Phase L's warnings are mostly prompts for inputs not given yet: one advisory per report,
            # every note in its detail, so they do not crowd the findings out of the verdict panel.
            run.checks.append({"code": f"{name.upper()}_NOTES", "key": f"{name.upper()}_NOTES", "severity": "info",
                               "section": "electrical", "title": f"{len(notes)} {name} note{'s' if len(notes) != 1 else ''} to read",
                               "detail": " ".join(f"- {w}" for w in notes), "_source": name})
    for item in (elec or {}).get("checklist") or []:
        if str(item.get("status") or "").lower() in ("fail", "missing"):
            run.checks.append({"code": "NEC680", "key": f"NEC680|{item.get('key')}", "severity": "warning", "section": "electrical",
                               "title": item.get("title") or "", "detail": item.get("note") or item.get("requirement") or "",
                               "citation": item.get("article") or "", "_source": "electrical"})

    # 10b structure
    struct = run.stage("structure", lambda: structural_takeoff(m, rules), None) if m.get("basins") else None
    for c in (struct or {}).get("checks") or []:
        run.checks.append({**c, "_source": "structural", "section": "basin" if c.get("section") in (None, "", "structure") else c["section"]})

    # 11 winterization (and heat when asked for)
    wint = run.stage("winterization", lambda: _as_dict(SYSTEM_MODULES["winterization"].calc(m, {}, rules)), {})
    run.result(wint)
    for c in run.stage("winterization checks", lambda: [x.to_dict() for x in SYSTEM_MODULES["winterization"].checks(m, {}, rules)], []):
        run.checks.append({**c, "_source": "family"})
    if (m.get("treatment") or {}).get("heat"):
        run.result(run.stage("heat", lambda: _as_dict(SYSTEM_MODULES["heat"].calc(m, {}, rules)), {}))
        for c in run.stage("heat checks", lambda: [x.to_dict() for x in SYSTEM_MODULES["heat"].checks(m, {}, rules)], []):
            run.checks.append({**c, "_source": "family"})

    # 12 outputs
    hyd = run.stage("bom", lambda: _as_dict(bom_engine.bom_rows(m, rules=rules)), {})
    run.result(hyd)
    for c in (hyd.get("value") or {}).get("checks") or []:
        run.checks.append({**c, "_source": "network"})
    lines = run.stage("bom merge", lambda: merge_bom((hyd.get("value") or {}).get("lines") or [], elec, ctl, struct, family_lines), [])
    bom = run.stage("prices", lambda: price_bom(lines, m, prices, rules), [])
    # The budget is the one the Budget print shows: the BOM subtotal plus the house-rule contingency.
    bom_subtotal = round(sum(r["amount"] for r in bom), 2)
    budget = prints.budget_summary(bom, budget_rules(rules))["total"]
    unpriced = sum(1 for r in bom if not r["rate"])
    if bom and unpriced:
        run.checks.append({"code": "BOM_UNPRICED", "key": "BOM_UNPRICED", "severity": "info", "section": "outputs",
                           "title": f"{unpriced} of {len(bom)} BOM lines have no price yet",
                           "remedy": "Pick Items for them (or set a valuation rate on the Item); the budget leaves them out.",
                           "_source": "engine"})

    op_rows = []
    for pid, op in ((network_result or {}).get("pumps") or {}).items():
        pump = next((p for p in (m.get("network") or {}).get("pumps") or [] if p.get("id") == pid), {})
        op_rows.append({"flow_gpm": op.get("flow_gpm"), "head_ft": op.get("head_ft"), "efficiency_pct": op.get("efficiency_pct"),
                        "motor_eff_pct": pump.get("motor_eff_pct"), "hours_per_day": hours, "electrical_kw": op.get("electrical_kw")})
    # A floating aerator moves no metered flow against a head: its power is phase L's motor kW over its hours.
    for row in ((elec or {}).get("energy") or {}).get("rows") or []:
        if row.get("kind") == "aerator" and _num(row.get("kw")):
            op_rows.append({"electrical_kw": _num(row["kw"]), "hours_per_day": _num(row.get("hours_day")) or 24.0})
    om = None
    if plan:
        equip_cost = sum(r["amount"] for r in bom if r["section"] == "Equipment") or None
        omr = run.stage("o&m", lambda: upkeep.om_per_year(plan, op_rows, volume_gal=total_gal, rules=rules, equipment_cost=equip_cost))
        om = run.result(omr).get("value") if omr else None
    maintenance = run.stage("maintenance", lambda: _maintenance(m, plan, bom, ctl, hours, outdoor=not indoor,
                                                                    frost=juris.winterize(site)), []) if plan else []

    # rollups
    ops = (network_result or {}).get("pumps") or {}
    pumps = (m.get("network") or {}).get("pumps") or []
    first = pumps[0] if pumps else {}
    op1 = ops.get(first.get("id")) or {}
    heads = [_num(o.get("head_ft")) for o in ops.values() if _num(o.get("head_ft")) is not None]
    kw = sum(_num(o.get("electrical_kw")) or 0.0 for o in ops.values()) or None
    rollups = {
        "design_flow_gpm": _round(design_gpm, 1),
        "operating_flow_gpm": _round(op_gpm, 1),
        "tdh_ft": _round(max(heads) if heads else None, 1),
        "operating_head_ft": _round(op1.get("head_ft"), 1),
        "selected_pump": first.get("item") or None,
        "selected_pump_label": _pump_label(first, ranking) if first else None,
        "pump_kw": _round(kw, 2),
        "total_basin_gallons": _round(total_gal, 1) if total_gal else None,
        # Not in the page's contract: the v1 column the Design dashboard's Hydraulic Headroom widget
        # filters on. The flow that circulates: the operating flow, else the design flow.
        "required_circulation_gpm": _round(op_gpm or design_gpm, 1),
        "budget_total": budget,
        "bom_subtotal": bom_subtotal,
        "om_cost_per_year": _round((om or {}).get("total"), 0),
    }

    # charts
    duty_state = run.stage("duty chart state", lambda: _duty_state(m, network_result, design_gpm, {**autosize.RULE_DEFAULTS, **rules}))
    duty_fn = getattr(drawings, "pump_duty_svg", None)
    charts = {
        "duty_svg": run.stage("duty chart", lambda: duty_fn(duty_state)) if (duty_fn and duty_state) else None,
        "schematic_svg": run.stage("schematic", lambda: _page_svg(drawings.circulation_schematic_svg(
            _schematic_state(m)))) if m.get("features") else None,
        "oneline_svg": run.stage("one-line", lambda: _page_svg((elec or {}).get("oneline_svg"))),
    }

    # outputs
    wdata = wint.get("data") or {}
    frost = juris.winterize(site) and not indoor
    om_lines = []
    if om:
        om_lines = [{"label": label, "amount": round(_num(om.get(k)) or 0.0, 0)} for k, label in
                    (("power", "Power"), ("water", "Water (make-up and refills)"), ("chemicals", "Chemicals"), ("parts", "Parts"))]
    outputs = {
        "schedules": [{"key": k, "label": label, "url": None} for k, label in SCHEDULES],
        "packet": {"label": "Submittal packet", "url": None},
        "om": {"total_per_year": rollups["om_cost_per_year"], "lines": om_lines},
        # The winterization module's own checklist (``data.winterize``), one title per step.
        "winterization": {"needed": bool(frost),
                          "steps": [str(i.get("title")) for i in wdata.get("winterize") or [] if isinstance(i, dict) and i.get("title")][:40]
                          if frost else ["Indoor feature or a site that does not freeze: no winterization. Drain only for service."]},
        "startup": _startup(m, regulated),
    }
    paper = run.stage("paper", lambda: paper_data(
        m, plan=plan, om=om, om_inputs=(run.results.get("om_per_year") or {}).get("inputs") or {}, maintenance=maintenance,
        wint=wdata, frost=bool(frost), iwf=iwf_lines, elec=elec, rules=rules, regulated=regulated, total_gal=total_gal,
        startup=outputs["startup"]), None)

    # issues, readiness, verdict
    for fail in run.failures:
        run.checks.append({"code": "ENGINE_STAGE_FAILED", "key": f"ENGINE_STAGE_FAILED|{fail['stage']}", "severity": "blocker",
                           "section": "features", "title": f"The {fail['stage']} step could not run",
                           "detail": fail["error"], "remedy": "Check the inputs this step reads; the Error Log has the trace.",
                           "_source": "engine"})
    issues = design_issues.model_issues(m, problems, run.checks)
    readiness = design_issues.model_readiness(m, issues, flows=flows, network=network_result)
    counts = design_issues.summarize(issues)
    rollups.update({
        "blocker_count": counts["blocker_count"],
        "warning_count": counts["warning_count"],
        "completion_percent": design_issues.completion_from_readiness(readiness),
        "issue_ready": bool(readiness.get("issue_ready")),
    })
    verdict = design_issues.model_verdict(issues, readiness)

    values = {
        "features": _feature_values(m, flows, network_result),
        "basins": _basin_values(m, volumes, basin_checks),
        "segments": _segment_values(network_result, m),
        "pumps": _pump_values(network_result, m),
    }
    m["schema"] = model_engine.SCHEMA
    return finite({
        "model": m,
        "results": run.results,
        "values": values,
        "rollups": rollups,
        "issues": issues,
        "readiness": readiness,
        "verdict": verdict,
        "bom": bom,
        "charts": charts,
        "outputs": outputs,
        "problems": problems,
        "electrical": {k: (elec or {}).get(k) for k in ELECTRICAL_KEYS} if elec else None,
        "structural": {k: (struct or {}).get(k) for k in ("totals", "rebar_schedule", "disclaimer")} if struct else None,
        "controls": dict({k: (ctl or {}).get(k) for k in CONTROLS_KEYS},
                         power_svg=_page_svg((ctl or {}).get("power_svg")),
                         ladder_svg=_page_svg((ctl or {}).get("ladder_svg"))) if ctl else None,
        "paper": paper,
    })
