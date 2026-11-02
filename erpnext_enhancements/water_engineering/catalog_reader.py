"""Read the Fountain Designer catalog as plain dicts.

This is the read interface the design engine's ``derive()`` uses for catalog data, so the
pure ``engine/`` never sees a Document. Three readers, one shape rule: every value is a
plain number, string, bool or list, dates are ISO strings, and a missing record is simply
absent from the result rather than an error.

* :func:`pump_curve_sets` — every curve of each pump Item, best first.
* :func:`nozzle_profiles` — Nozzle Profiles with their performance points.
* :func:`light_specs` — the light fields of light Items.

What these are careful about:

* **Pump Curve records win; the Item's legacy table is the fallback.** Before v1.758.0 a
  curve lived on the Item as ``custom_pump_curve``. An Item with any Pump Curve record is
  read from those records only; an Item with none is read from its legacy table, labeled
  ``source = "Legacy item table"`` so a design can say so.
* **"Best first" is a ranking, not a choice.** A curve with fewer than two points cannot be
  interpolated and sorts last; then full speed (``speed_pct`` 100) before reduced speed,
  then a sourced curve before a generic estimate, then the larger impeller, then the most
  recently changed. ``api/water_design.pump_curves`` takes the first; ``derive()`` may look
  at all of them (a VFD design wants the reduced-speed curves too).
* **Unknown reads as None, not 0.** Child Float columns are NOT NULL DEFAULT 0, so a point
  saved without efficiency, NPSHr or power stores 0, and 162 of the 169 research pumps publish
  none of the three. :func:`_point` turns those zeros back into None (efficiency at zero flow
  excepted: it is 0 at shutoff by definition), and a nozzle row's spread and throw likewise. A
  ``derive()`` reading ``npshr_ft: 0`` as "needs no suction head" would pass every NPSH check.
* **Never raises on a site that has not migrated yet.** ``doc_events`` and the designer's
  save can run during a migrate, before the new tables or columns exist, so each read falls back to
  less (the legacy fields, or nothing) instead of failing the caller.
"""

import frappe

CURVE_FIELDS = [
    "name",
    "item",
    "curve_label",
    "source",
    "impeller_dia_in",
    "rpm",
    "speed_pct",
    "max_flow_gpm",
    "shutoff_head_ft",
    "source_url",
    "source_document",
    "source_page",
    "verified_by",
    "verified_on",
    "read_method",
    "error_band_pct",
    "catalog_id",
]
POINT_FIELDS = ["parent", "flow_gpm", "head_ft", "efficiency_pct", "npshr_ft", "power_bhp"]
LEGACY_SOURCE = "Legacy item table"
GENERIC_SOURCE = "Generic estimate"

NOZZLE_FIELDS = [
    "name",
    "profile_name",
    "item",
    "manufacturer",
    "model_no",
    "nozzle_type",
    "is_generic_estimate",
    "connection_size",
    "level_sensitive",
    "min_depth_in",
    "max_depth_in",
    "recommended_beam_deg",
    "discharge_coefficient",
    "orifice_diameter_in",
    "orifice_area_in2",
    "rated_gpm",
    "rated_head_ft",
    "source_url",
    "source_document",
    "source_page",
    "verified_by",
    "verified_on",
    "read_method",
    "error_band_pct",
    "catalog_id",
]
#: The Nozzle Profile columns that predate v1.758.0, read when the new ones are not there yet.
NOZZLE_LEGACY_FIELDS = [
    "name",
    "profile_name",
    "manufacturer",
    "model_no",
    "nozzle_type",
    "is_generic_estimate",
    "discharge_coefficient",
    "orifice_diameter_in",
    "orifice_area_in2",
    "rated_gpm",
    "rated_head_ft",
]
NOZZLE_POINT_FIELDS = [
    "parent",
    "head_ft",
    "psi",
    "flow_gpm",
    "display_height_ft",
    "display_diameter_ft",
    "throw_ft",
    "depth_over_nozzle_in",
]

#: Item field -> key in a :func:`light_specs` dict.
LIGHT_FIELDS = {
    "item_name": "item_name",
    "custom_model_no": "model_no",
    "brand": "manufacturer",
    "custom_elec_watts": "watts",
    "custom_elec_voltage": "voltage",
    "custom_lumens": "lumens",
    "custom_beam_deg": "beam_deg",
    "custom_light_control": "control",
    "custom_max_depth_ft": "max_depth_ft",
    "custom_ip_rating": "ip_rating",
    "custom_ul_listing": "listing",
    "custom_requires_gfci": "requires_gfci",
    "custom_generic_estimate": "is_generic_estimate",
    "custom_spec_source_url": "source_url",
    "custom_spec_revision": "source_document",
    "custom_spec_verified_by": "verified_by",
    "custom_spec_verified_on": "verified_on",
    "custom_catalog_id": "catalog_id",
    "custom_spec_confidence": "confidence",
    "custom_spec_method": "read_method",
    "custom_spec_error_band_pct": "error_band_pct",
}
_BOOL_KEYS = {"is_generic_estimate", "level_sensitive", "requires_gfci"}


def _codes(values):
    return [v for v in dict.fromkeys(values or []) if v]


def _clean(row, keys):
    """A plain dict of ``keys`` from a row: dates as ISO strings, flags as bools."""
    out = {}
    for key in keys:
        value = row.get(key)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        if key in _BOOL_KEYS:
            value = bool(value)
        out[key] = value
    return out


#: Optional point values a stored 0 means "not published" for. See the module docstring.
UNKNOWN_IF_ZERO = {"efficiency_pct", "npshr_ft", "power_bhp", "display_diameter_ft", "throw_ft"}


def _point(row, keys):
    point = {k: row.get(k) for k in keys if k != "parent"}
    for key in UNKNOWN_IF_ZERO & set(point):
        if point[key] in (0, 0.0, "") and not (key == "efficiency_pct" and not point.get("flow_gpm")):
            point[key] = None
    return point


def curve_rank(curve):
    """Sort key for :func:`pump_curve_sets` (smaller is better). See the module docstring."""
    speed = curve.get("speed_pct")
    return (
        len(curve.get("points") or []) < 2,
        speed not in (None, "", 0) and float(speed) != 100.0,
        curve.get("source") in (GENERIC_SOURCE, LEGACY_SOURCE),
        -(float(curve.get("impeller_dia_in") or 0)),
    )


def pump_curve_sets(item_codes):
    """``{item_code: [curve, ...]}`` best first, for the given pump Items.

    Each curve is ``{name, item, curve_label, source, impeller_dia_in, rpm, speed_pct,
    max_flow_gpm, shutoff_head_ft, source_url, source_document, source_page, verified_by,
    verified_on, read_method, error_band_pct, catalog_id, is_generic_estimate, points: [{flow_gpm, head_ft, efficiency_pct,
    npshr_ft, power_bhp}, ...]}`` with points in rising flow. A legacy-table curve has
    ``name`` None and ``source`` "Legacy item table". Items with no curve are absent.
    """
    codes = _codes(item_codes)
    if not codes:
        return {}
    out = {}
    try:
        curves = frappe.get_all(
            "Pump Curve",
            filters={"item": ["in", codes]},
            fields=CURVE_FIELDS,
            order_by="modified desc",
        )
    except Exception:
        curves = []
    if curves:
        try:
            points = frappe.get_all(
                "Pump Curve Point",
                filters={
                    "parenttype": "Pump Curve",
                    "parentfield": "points",
                    "parent": ["in", [c["name"] for c in curves]],
                },
                fields=POINT_FIELDS,
                order_by="parent asc, idx asc",
            )
        except Exception:
            points = []
        by_parent = {}
        for p in points:
            by_parent.setdefault(p["parent"], []).append(_point(p, POINT_FIELDS))
        for c in curves:
            curve = _clean(c, CURVE_FIELDS)
            curve["is_generic_estimate"] = curve.get("source") == GENERIC_SOURCE
            curve["points"] = sorted(by_parent.get(c["name"], []), key=lambda p: float(p.get("flow_gpm") or 0))
            out.setdefault(c["item"], []).append(curve)

    missing = [c for c in codes if c not in out]
    if missing:
        for item_code, pts in _legacy_points(missing).items():
            out[item_code] = [
                {
                    **{k: None for k in CURVE_FIELDS},
                    "item": item_code,
                    "curve_label": "Item curve table",
                    "source": LEGACY_SOURCE,
                    "speed_pct": 100,
                    "is_generic_estimate": False,
                    "points": pts,
                }
            ]
    for item_code in out:
        out[item_code].sort(key=curve_rank)
    return out


def _legacy_points(item_codes):
    """``{item_code: [point, ...]}`` from the Items' ``custom_pump_curve`` tables."""
    try:
        rows = frappe.get_all(
            "Pump Curve Point",
            filters={"parent": ["in", item_codes], "parenttype": "Item", "parentfield": "custom_pump_curve"},
            fields=POINT_FIELDS,
            order_by="parent asc, idx asc",
        )
    except Exception:
        try:
            # Before v1.758.0 the child table had only these two columns.
            rows = frappe.get_all(
                "Pump Curve Point",
                filters={"parent": ["in", item_codes], "parenttype": "Item", "parentfield": "custom_pump_curve"},
                fields=["parent", "flow_gpm", "head_ft"],
                order_by="parent asc, idx asc",
            )
        except Exception:
            return {}
    out = {}
    for r in rows:
        out.setdefault(r["parent"], []).append(_point(r, POINT_FIELDS))
    for pts in out.values():
        pts.sort(key=lambda p: float(p.get("flow_gpm") or 0))
    return out


def nozzle_profiles(names):
    """``{profile_name: profile}`` for the given Nozzle Profile names.

    Each profile is the :data:`NOZZLE_FIELDS` values (flags as bools, dates as ISO strings)
    plus ``points``: ``[{head_ft, psi, flow_gpm, display_height_ft, display_diameter_ft,
    throw_ft, depth_over_nozzle_in}, ...]`` sorted by depth over the nozzle, then head.
    Empty ``points`` means the profile has only the Cd or rated-GPM method.
    """
    names = _codes(names)
    if not names:
        return {}
    fields = NOZZLE_FIELDS
    try:
        rows = frappe.get_all("Nozzle Profile", filters={"name": ["in", names]}, fields=fields)
    except Exception:
        fields = NOZZLE_LEGACY_FIELDS
        try:
            rows = frappe.get_all("Nozzle Profile", filters={"name": ["in", names]}, fields=fields)
        except Exception:
            return {}
    try:
        points = frappe.get_all(
            "Nozzle Performance Point",
            filters={"parenttype": "Nozzle Profile", "parent": ["in", names]},
            fields=NOZZLE_POINT_FIELDS,
            order_by="parent asc, idx asc",
        )
    except Exception:
        points = []
    by_parent = {}
    for p in points:
        by_parent.setdefault(p["parent"], []).append(_point(p, NOZZLE_POINT_FIELDS))
    out = {}
    for r in rows:
        profile = {k: None for k in NOZZLE_FIELDS}
        profile.update(_clean(r, fields))
        for key in _BOOL_KEYS & set(profile):
            profile[key] = bool(profile[key])
        profile["points"] = sorted(
            by_parent.get(r["name"], []),
            key=lambda p: (float(p.get("depth_over_nozzle_in") or 0), float(p.get("head_ft") or 0)),
        )
        out[r["name"]] = profile
    return out


def light_specs(item_codes):
    """``{item_code: spec}`` for light Items; keys are the values of :data:`LIGHT_FIELDS`.

    ``watts`` and ``voltage`` come from the shared electrical fields (``custom_elec_*``), the
    rest from the light block. A field this site has not migrated yet reads as None.
    """
    codes = _codes(item_codes)
    if not codes:
        return {}
    try:
        present = [f for f in LIGHT_FIELDS if frappe.db.has_column("Item", f)]
        rows = frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", *present])
    except Exception:
        return {}
    out = {}
    for r in rows:
        spec = {key: None for key in LIGHT_FIELDS.values()}
        for field in present:
            value = r.get(field)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            spec[LIGHT_FIELDS[field]] = value
        for key in _BOOL_KEYS & set(spec):
            spec[key] = bool(spec[key])
        spec["item_code"] = r["name"]
        out[r["name"]] = spec
    return out
