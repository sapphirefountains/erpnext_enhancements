# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Typed design issues + per-section readiness for Water Feature Design.

The calc engine speaks in free-form ``CalcResult.status`` strings and warning
sentences — great for the audit trail, unfindable for a designer who just needs
to know *what is wrong and where*. This module is the single producer of two
derived, typed structures the form / wizard / list view / prints / Triton all
consume:

* ``build_issues(doc)`` — a flat list of ``DesignIssue`` dicts: severity-coded
  (``blocker`` = legal/safety/physical failure, ``warning`` = engineering
  limit, ``info`` = advisory), section-keyed, row-addressable (so the UI can
  jump to the offending row), each with a fix hint and the source citation.
* ``build_readiness(doc, issues)`` — per-section completeness with two gates:
  ``calc_ready`` (everything the hydraulic spine needs) and ``issue_ready``
  (the DOC-0121 design-package gate: calc-complete + electrical/chemistry/
  drainage captured + fittings on the piping + zero blockers).

Layering: the engine's 35 calcs are untouched. Row-scoped issues are derived
from the computed row fields the controller already persists
(``velocity_status`` / ``pressure_status`` / ``flow_gpm``); doc-scoped issues
are mapped from the persisted ``calc_results`` audit rows via ``STATUS_RULES``
/ ``WARNING_RULES``. Because everything derives from *persisted* state, the
same functions serve live recompute, the no-save preview, and the migration
backfill (no engine re-run, no numeric churn on submitted designs).

Issue keys are ``CODE|row label`` — labels, not child-row names — because
``save_inputs`` recreates child rows wholesale (names churn every save) and
acknowledgements must survive both the preview→save transition and re-saves.

This module deliberately imports no frappe (stub-testable like the engine).
"""

from __future__ import annotations

import json
import zlib

from erpnext_enhancements.water_engineering.engine.constants import (
    CIT_CHEM_TARGETS,
    CIT_GRAVITY,
    CIT_PIPE,
    CIT_PIPE_SPECS,
    WEIR_OPERATE_GPM_PER_FT,
)
from erpnext_enhancements.water_engineering.engine.aquatic import is_regulated
from erpnext_enhancements.water_engineering.engine.feature import feature_flow_category

BLOCKER = "blocker"
WARNING = "warning"
INFO = "info"

_SEVERITY_ORDER = {BLOCKER: 0, WARNING: 1, INFO: 2}

# Section vocabulary — shared with the wizard's step keys (PR2) and the Design
# Health panel's grouping. Order = the DOC-0119 design procession.
SECTIONS = [
    ("project", "Concept & Site"),
    ("basin", "Basin"),
    ("features", "Water Features"),
    ("tiers", "Tiers"),
    ("piping", "Piping"),
    ("pump", "Pump"),
    ("electrical", "Electrical"),
    ("safety", "Safety"),
    ("chemistry", "Chemistry"),
    ("drainage", "Drainage & Surge"),
    ("regulated", "Regulated Compliance"),
    ("deliverables", "Design Package"),
]
SECTION_LABELS = dict(SECTIONS)

# Which section a calc's findings belong to when mapped from calc_results.
_CALC_SECTION = {
    "basin_volume": "basin",
    "turnover_gpm": "basin",
    "weir_flow": "features",
    "nozzle_flow": "features",
    "nozzle_array_flow": "features",
    "tiered_fountain_flow": "features",
    "total_dynamic_head": "piping",
    "pipe_pressure_check": "piping",
    "pipe_pressure_rating": "piping",
    "select_pump": "pump",
    "chlorinator_feed": "chemistry",
    "chemistry_targets": "chemistry",
    "ozone_sidestream": "chemistry",
    "manning_drain_flow": "drainage",
    "size_drain": "drainage",
    "surge_basin_volume": "drainage",
    "suction_outlet_vgb": "safety",
    "npsh_available": "safety",
    "water_hammer": "safety",
    "turnover_time": "regulated",
    "minimum_flow_rate": "regulated",
    "bather_load": "regulated",
    "skimmer_sizing": "regulated",
    "main_drain_flow": "safety",
}

# ------------------------------------------------------------------ rules
# (calc match, pattern-in-status, code, severity, section, fix_hint).
# Calc match is exact OR a prefix ending in "—" (the per-segment envelopes are
# renamed "Pipe friction — <label>" etc.). Patterns are case-insensitive
# substrings of the free-form engine strings — the strings themselves are the
# persisted truth and are never rewritten.
STATUS_RULES = [
    ("pipe_pressure_check", "exceeds pressure", "PIPE_PRESSURE_UNDER_RATED", BLOCKER, "piping",
     "Use a heavier wall (SCH80), a larger size, or reduce TDH."),
    ("water_hammer", "exceeds pipe rating", "WATER_HAMMER_OVER_RATING", BLOCKER, "safety",
     "Slow the valve closure, add a surge arrestor, or up-rate the pipe."),
    ("suction_outlet_vgb", "entrapment", "VGB_ENTRAPMENT", BLOCKER, "safety",
     "Use a larger / higher-rated listed cover, add outlets, or reduce flow."),
    ("suction_outlet_vgb", "add second drain", "VGB_SINGLE_OUTLET", WARNING, "safety",
     "Provide two outlets >3 ft apart, each rated for the full flow."),
    ("npsh_available", "cavitation", "NPSH_CAVITATION", BLOCKER, "safety",
     "Flood the suction, shorten/enlarge the suction pipe, or cool the water."),
    ("npsh_available", "marginal", "NPSH_MARGINAL", WARNING, "safety",
     "Tighten suction piping or lower the pump to regain the 2-3 ft margin."),
    ("ozone_sidestream", "need larger", "CHEM_CONTACT_TANK_UNDERSIZED", WARNING, "chemistry",
     "Select a larger or additional ozone contact tank."),
]

WARNING_RULES = [
    ("select_pump", "no supplied pump covers", "PUMP_NOT_RESOLVED", WARNING, "pump",
     "Add a larger pump candidate, enter curve points, or split the load."),
    ("select_pump", "matched on flow only", "PUMP_FLOW_ONLY", WARNING, "pump",
     "Confirm the duty point on the manufacturer curve, or enter Pump Curve points on the Item."),
    ("chemistry_targets", "floor", "CHEM_FC_BELOW_CYA_FLOOR", WARNING, "chemistry",
     "Raise the free-chlorine target or lower the stabilizer (CYA)."),
    ("total_dynamic_head", "rated to", "COMPONENT_OVER_MAX_FLOW", WARNING, "piping",
     "Split the flow across parallel components or size the component up."),
    ("component loss —", "rated to", "COMPONENT_OVER_MAX_FLOW", WARNING, "piping",
     "Split the flow across parallel components or size the component up."),
    ("total_dynamic_head", "unknown fitting", "PIPE_UNKNOWN_FITTING", WARNING, "piping",
     "Re-pick the fitting from the catalog dialog (typed names don't match the K table)."),
    ("fitting loss —", "unknown fitting", "PIPE_UNKNOWN_FITTING", WARNING, "piping",
     "Re-pick the fitting from the catalog dialog (typed names don't match the K table)."),
    ("total_dynamic_head", "unknown component", "PIPE_UNKNOWN_COMPONENT", WARNING, "piping",
     "Re-pick the component from the catalog dialog."),
    ("component loss —", "unknown component", "PIPE_UNKNOWN_COMPONENT", WARNING, "piping",
     "Re-pick the component from the catalog dialog."),
    ("total_dynamic_head", "no pipe diameter", "SEG_NO_SIZE", WARNING, "piping",
     "Set the segment's nominal size so its losses count toward TDH."),
    ("weir_flow", "went negative", "WEIR_HEAD_TOO_SMALL", WARNING, "features",
     "Increase the head over the crest or shorten it."),
    ("manning_drain_flow", "outside the", "DRAIN_SLOPE_OUT_OF_RANGE", WARNING, "drainage",
     "Keep gravity-drain slope between 1/16 and 1/2 in/ft (DOC-0119)."),
    ("water_hammer", "exceeds the pipe", "WATER_HAMMER_OVER_RATING", BLOCKER, "safety",
     "Slow the valve closure, add a surge arrestor, or up-rate the pipe."),
]

# Engine warning sentences that are caveats/prompts, not findings — never issues.
# (Most only occur in stateless calculator runs, but the mapper stays safe.)
_QUIET_WARNINGS = (
    "engineering aid only",
    "no pump catalog supplied",
    "needs a nozzle profile",  # row-scoped FEATURE_NEEDS_PROFILE covers it with a row ref
    "below the ~",  # row-scoped WEIR_UNDER_SHEETED covers it with a row ref
    "add at least one tier",  # readiness covers it
    "no pump npshr supplied",
    "no closure time supplied",
    "must be > 0",
    "must be >= 0",
)


def _flt(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows(doc, table):
    return doc.get(table) or []


def _issue(code, severity, title, section, *, scope="", detail="", ref=None,
           fix_hint="", citation="", calc=""):
    return {
        "key": f"{code}|{scope}" if scope else code,
        "code": code,
        "severity": severity,
        "title": title,
        "detail": (detail or "")[:600],
        "section": section,
        "ref": ref,
        "fix_hint": fix_hint,
        "citation": citation,
        "calc": calc,
    }


def _row_ref(table, row, idx, field=None):
    return {
        "table": table,
        "row_name": getattr(row, "name", None) or None,
        "row_idx": idx,
        "field": field,
    }


def _seg_label(row, idx):
    return getattr(row, "segment_label", None) or f"segment {idx + 1}"


def _feat_label(row, idx):
    return getattr(row, "feature_label", None) or f"feature {idx + 1}"


# ------------------------------------------------------------- row-scoped


def _segment_issues(doc):
    issues = []
    design_flow = _flt(doc.get("design_flow_gpm"))
    for idx, row in enumerate(_rows(doc, "pipe_segments")):
        label = _seg_label(row, idx)
        vstat = (getattr(row, "velocity_status", "") or "").lower()
        vfps = _flt(getattr(row, "velocity_fps", 0))
        size = getattr(row, "nominal_size", None)
        material = getattr(row, "material", None) or doc.get("pipe_material") or "SCH40 PVC"

        if "exceeds legal" in vstat:
            issues.append(_issue(
                "PIPE_VEL_EXCEEDS_LEGAL", BLOCKER,
                f"{label}: {vfps:.1f} fps exceeds the legally-defensible velocity limit",
                "piping", scope=label,
                detail=f"{material} {size or ''} carrying this flow runs past the legal band "
                       "(erosion / noise / liability).",
                ref=_row_ref("pipe_segments", row, idx, "nominal_size"),
                fix_hint="Increase the nominal size or split the flow across parallel runs.",
                citation=CIT_PIPE, calc="pipe_velocity",
            ))
        elif "increase" in vstat:
            issues.append(_issue(
                "PIPE_VEL_OVER_LIMIT", WARNING,
                f"{label}: {vfps:.1f} fps is over the recommended velocity limit",
                "piping", scope=label,
                detail=f"{material} {size or ''} runs above the design band "
                       "(suction 4.5 / discharge 6.5 fps PVC).",
                ref=_row_ref("pipe_segments", row, idx, "nominal_size"),
                fix_hint="Increase the nominal size one step.",
                citation=CIT_PIPE, calc="pipe_velocity",
            ))
        elif "below self-cleaning" in vstat:
            issues.append(_issue(
                "PIPE_VEL_SETTLING", INFO,
                f"{label}: {vfps:.2f} fps is below the ~0.5 fps self-cleaning velocity",
                "piping", scope=label,
                detail="Solids settle out in slow horizontal runs.",
                ref=_row_ref("pipe_segments", row, idx, "nominal_size"),
                fix_hint="Consider one size smaller, or accept it and plan periodic flushing.",
                citation=CIT_PIPE, calc="pipe_velocity",
            ))
        elif size and not vstat and not vfps:
            issues.append(_issue(
                "PIPE_NO_SPEC", WARNING,
                f"{label}: no pipe spec on file for {material} {size}",
                "piping", scope=label,
                detail="Velocity and friction cannot be computed for this material/size combination.",
                ref=_row_ref("pipe_segments", row, idx, "nominal_size"),
                fix_hint="Pick a listed material + nominal size.",
                citation=CIT_PIPE_SPECS, calc="pipe_velocity",
            ))

        pstat = (getattr(row, "pressure_status", "") or "").lower()
        if "exceeds" in pstat:
            margin = _flt(getattr(row, "pressure_margin_psi", 0))
            issues.append(_issue(
                "PIPE_PRESSURE_UNDER_RATED", BLOCKER,
                f"{label}: {material} {size or ''} is under-rated for the system pressure",
                "piping", scope=label,
                detail=f"Rated-minus-system margin is {margin:.0f} psi.",
                ref=_row_ref("pipe_segments", row, idx, "material"),
                fix_hint="Use a heavier wall (SCH80), a larger size, or reduce TDH.",
                citation=CIT_PIPE_SPECS, calc="pipe_pressure_check",
            ))

        if (_flt(getattr(row, "pipe_length_ft", 0)) > 0
                and not _flt(getattr(row, "flow_gpm", 0)) and not design_flow):
            issues.append(_issue(
                "SEG_NO_FLOW", WARNING,
                f"{label}: no flow and no design flow to infer it — friction loss is zero",
                "piping", scope=label,
                ref=_row_ref("pipe_segments", row, idx, "flow_gpm"),
                fix_hint="Enter the GPM this segment carries (or complete basin/features so "
                         "the design flow can carry through).",
                citation=CIT_PIPE, calc="total_dynamic_head",
            ))
    return issues


def _feature_issues(doc):
    issues = []
    for idx, row in enumerate(_rows(doc, "features")):
        label = _feat_label(row, idx)
        category = feature_flow_category(getattr(row, "feature_type", "") or "")
        flow = _flt(getattr(row, "flow_gpm", 0))
        if category == "orifice" and not flow:
            issues.append(_issue(
                "FEATURE_NEEDS_PROFILE", WARNING,
                f"{label}: orifice nozzle needs a Nozzle Profile and a supply head",
                "features", scope=label,
                detail="Orifice flow needs sourced coefficients (Cd + orifice size, or a "
                       "rated GPM @ head) — the engine will not invent a number.",
                ref=_row_ref("features", row, idx, "nozzle_profile"),
                fix_hint="Pick a Nozzle Profile and enter the supply head on the feature row.",
                citation="Nozzle Profile catalog (manufacturer cut sheets)", calc="nozzle_flow",
            ))
        elif category == "weir":
            length = _flt(getattr(row, "weir_length_ft", 0))
            if length > 0 and flow > 0 and (flow / length) < WEIR_OPERATE_GPM_PER_FT:
                issues.append(_issue(
                    "WEIR_UNDER_SHEETED", WARNING,
                    f"{label}: edge runs at {flow / length:.2f} GPM/ft — below the "
                    f"~{WEIR_OPERATE_GPM_PER_FT:g} GPM/ft continuous-sheet minimum",
                    "features", scope=label,
                    detail="The sheet may break into rivulets.",
                    ref=_row_ref("features", row, idx, "head_in"),
                    fix_hint="Increase the head over the crest or shorten the crest.",
                    citation="DOC-0049 / B - Surge Basin (edge sheet rate)", calc="weir_flow",
                ))
    return issues


# ------------------------------------------------------------- doc-scoped


def _match_calc(rule_calc, calc_name):
    calc_l = (calc_name or "").lower()
    rule_l = rule_calc.lower()
    if rule_l.endswith("—"):
        return calc_l.startswith(rule_l[:-1].strip())
    return calc_l == rule_l or calc_l.startswith(rule_l + " —")


def _calc_result_issues(doc):
    """Map persisted calc_results rows (status bands + warning sentences) to
    typed issues. Works identically on live recompute, preview, and backfill."""
    issues = []
    # Per-row pressure_status supersedes the spine's doc-level pressure envelope
    # (same finding, but row-addressable). Skip the doc-level one when rows carry it.
    rows_have_pressure = any(
        (getattr(r, "pressure_status", "") or "") for r in _rows(doc, "pipe_segments")
    )
    cya_user_basis = bool(_flt(doc.get("chem_cya_ppm")))
    # The TDH rollup re-copies every per-segment envelope's warnings (fitting /
    # component sentences) — suppress those exact duplicates on the rollup row so
    # one finding yields one issue (keyed to the segment-scoped envelope).
    segment_envelope_warnings = {
        w
        for r in _rows(doc, "calc_results")
        if (getattr(r, "calc", "") or "").lower().startswith(("fitting loss", "component loss", "pipe friction"))
        for w in (getattr(r, "warnings", "") or "").split("\n")
        if w
    }

    for r in _rows(doc, "calc_results"):
        calc = getattr(r, "calc", "") or ""
        status = (getattr(r, "status", "") or "").lower()
        warnings = [w for w in (getattr(r, "warnings", "") or "").split("\n") if w]
        citation = getattr(r, "citations", "") or ""
        section = _CALC_SECTION.get(calc.split(" —")[0].strip().lower(), "piping")
        matched_warnings = set()

        if status and "okay" not in status:
            for rule_calc, pattern, code, severity, rule_section, fix in STATUS_RULES:
                if _match_calc(rule_calc, calc) and pattern in status:
                    if code == "PIPE_PRESSURE_UNDER_RATED" and rows_have_pressure:
                        # Superseded by the row-addressable version — swallow the
                        # row's warning sentences too, or they leak back as notes.
                        matched_warnings.update(warnings)
                        break
                    issues.append(_issue(
                        code, severity,
                        f"{calc}: {getattr(r, 'status', '')}",
                        rule_section, scope=calc,
                        detail="\n".join(warnings),
                        fix_hint=fix, citation=citation, calc=calc,
                    ))
                    matched_warnings.update(warnings)
                    break

        for w in warnings:
            if w in matched_warnings:
                continue
            if calc.lower() == "total_dynamic_head" and w in segment_envelope_warnings:
                continue
            wl = w.lower()
            if any(q in wl for q in _QUIET_WARNINGS):
                continue
            # Scope per warning SENTENCE (not per calc): one calc row can carry
            # several distinct findings (two components over max), and the same
            # code must not collapse them — nor let one ack cover them all. The
            # sentence is deterministic engine output (hashed to keep the key
            # short — acks store it in a bounded field), so keys stay stable
            # across re-saves and correctly change when the numbers change.
            w_scope = f"{calc[:80]}|{zlib.crc32(w.encode('utf-8')) & 0xFFFFFFFF:08x}"
            for rule_calc, pattern, code, severity, rule_section, fix in WARNING_RULES:
                if _match_calc(rule_calc, calc) and pattern in wl:
                    if code == "CHEM_FC_BELOW_CYA_FLOOR":
                        # "Free chlorine X ppm is below the floor" is inherently a
                        # user-FC-basis finding and stays a warning. The "At CYA X
                        # ppm the floor is above the standard target" variant only
                        # gates when the CYA figure is the user's own — on the
                        # default basis it's an advisory.
                        if not wl.startswith("free chlorine") and not cya_user_basis:
                            severity = INFO
                            fix = ("Enter the planned CYA / free-chlorine levels on the "
                                   "Treatment tab to check the real floor.")
                    issues.append(_issue(
                        code, severity, w, rule_section, scope=w_scope,
                        ref={"table": None, "row_name": None, "row_idx": None,
                             "field": "chem_cya_ppm"} if code == "CHEM_FC_BELOW_CYA_FLOOR" else None,
                        fix_hint=fix, citation=citation, calc=calc,
                    ))
                    break
            else:
                # Unmatched engine warning: surface as an advisory rather than
                # dropping it silently (the audit trail keeps the full text).
                issues.append(_issue(
                    "ENGINE_NOTE", INFO, w, section, scope=w_scope,
                    citation=citation, calc=calc,
                ))
    return issues


def _drainage_issues(doc):
    issues = []
    if _flt(doc.get("drain_capacity_gpm")) > 0:
        issues.append(_issue(
            "DRAIN_BASIS_DIVERGENCE", INFO,
            "Drain capacity uses the conservative half-full basis (DOC-0049)",
            "drainage",
            detail="DOC-0119's drainage tables are full-pipe and read ~2-3x higher for the "
                   "same size/slope. The engine keeps the conservative figure as the "
                   "authority; expect the guideline tables to look bigger.",
            ref={"table": None, "row_name": None, "row_idx": None, "field": "drain_capacity_gpm"},
            citation=f"{CIT_GRAVITY} ; DOC-0119 drainage tables", calc="manning_drain_flow",
        ))
    return issues


def build_issues(doc, extra=None):
    """All typed issues for a design, most severe first. ``extra`` lets the
    controller contribute issues only it can know (e.g. catalog lookups)."""
    issues = []
    issues += _segment_issues(doc)
    issues += _feature_issues(doc)
    issues += _calc_result_issues(doc)
    issues += _drainage_issues(doc)
    issues += list(extra or [])

    # De-dup by key (a finding can surface through more than one path).
    seen, unique = set(), []
    for i in issues:
        if i["key"] in seen:
            continue
        seen.add(i["key"])
        unique.append(i)
    unique.sort(key=lambda i: (_SEVERITY_ORDER.get(i["severity"], 3),))
    return unique


def calc_error_issue(detail=""):
    """The blocker recorded when recompute itself fails."""
    return _issue(
        "CALC_ERROR", BLOCKER, "Calculation error — the engine could not run this design",
        "project", detail=detail or "See the Error Log.",
        fix_hint="Fix the inputs the Error Log points at, then save again.",
    )


# ------------------------------------------------------------- readiness


def acked_keys(doc):
    return {getattr(a, "issue_key", None) for a in _rows(doc, "issue_acks")}


def unacknowledged_warnings(doc, issues):
    acked = acked_keys(doc)
    return [i for i in issues if i["severity"] == WARNING and i["key"] not in acked]


def _missing(label, why, field=None, unlocks=None):
    return {"label": label, "why": why, "field": field, "unlocks": unlocks}


def build_readiness(doc, issues):
    """Per-section readiness + the two gates.

    ``calc_ready`` = the hydraulic spine has everything it needs (the same four
    milestones ``completion_percent`` tracks, itemized). ``issue_ready`` = the
    DOC-0121 design-package gate: calc-ready + electrical / chemistry / drainage
    captured + fittings on the piping + zero blockers + no unacknowledged
    warnings.
    """
    blocked_sections = {i["section"] for i in issues if i["severity"] == BLOCKER}
    sections = []

    def add(key, gate, missing, n_a=False):
        state = "n/a" if n_a else ("blocked" if key in blocked_sections else
                                   ("complete" if not missing else "incomplete"))
        sections.append({
            "key": key,
            "label": SECTION_LABELS.get(key, key),
            "gate": gate,
            "state": state,
            "missing": missing,
        })

    # -- calc gate (the hydraulic model) --------------------------------
    basins_ok = any(_flt(getattr(b, "volume_gal", 0)) > 0 for b in _rows(doc, "basins"))
    add("basin", "calc", [] if basins_ok else [_missing(
        "A basin with dimensions", "Volume drives circulation, chemistry, and surge sizing.",
        field="basins", unlocks="turnover_gpm")])

    features_ok = any(_flt(getattr(f, "flow_gpm", 0)) > 0 for f in _rows(doc, "features"))
    add("features", "calc", [] if features_ok else [_missing(
        "A water feature with computable flow",
        "Feature flow vs. circulation sets the design flow the piping and pump size to.",
        field="features", unlocks="design flow")])

    tiered_needed = any(
        feature_flow_category(getattr(f, "feature_type", "") or "") == "tiered"
        for f in _rows(doc, "features")
    )
    tiers_ok = any(_flt(getattr(t, "diameter_in", 0)) > 0 for t in _rows(doc, "tiers"))
    add("tiers", "calc", [] if (not tiered_needed or tiers_ok) else [_missing(
        "Tier rows (one diameter per bowl)",
        "A cascade is sized by its largest rim; the tiers table is its geometry.",
        field="tiers", unlocks="tiered_fountain_flow")], n_a=not tiered_needed)

    piping_ok = any(getattr(s, "nominal_size", None) for s in _rows(doc, "pipe_segments"))
    add("piping", "calc", [] if piping_ok else [_missing(
        "At least one pipe segment (size + length)",
        "Segments carry the friction losses that build TDH and the velocity checks.",
        field="pipe_segments", unlocks="total_dynamic_head")])

    pump_ok = bool(doc.get("selected_pump")) or bool(_rows(doc, "pumps"))
    add("pump", "calc", [] if pump_ok else [_missing(
        "A pump candidate (or rated pumps in the Items catalog)",
        "The design flow + TDH duty point picks the pump.",
        field="pumps", unlocks="select_pump")])

    # -- issue (design-package) gate -------------------------------------
    add("project", "issue", [] if doc.get("design_title") else [_missing(
        "A design title", "Titles the design on every package print.",
        field="design_title")])

    add("electrical", "issue", [] if _rows(doc, "electrical_loads") else [_missing(
        "Electrical loads (HP / voltage / FLA)",
        "Feeds the panel + breaker schedule in the design package.",
        field="electrical_loads")])

    add("chemistry", "issue", [] if _flt(doc.get("total_basin_gallons")) > 0 else [_missing(
        "Basin volume (chemistry sizes off it)",
        "Chlorinator feed and target ranges are computed from the system volume.",
        field="basins")])

    add("drainage", "issue", [] if doc.get("drain_nominal_size") else [_missing(
        "A drain size (+ slope)",
        "The package needs a gravity-drain capacity check (Manning's).",
        field="drain_nominal_size")])

    # Regulated aquatic venues (pools/spas) only — n/a for decorative fountains.
    regulated = is_regulated(doc.get("venue_type") or "")
    reg_missing = []
    if regulated:
        surface_area = sum(_flt(getattr(b, "surface_area_sf", 0)) for b in _rows(doc, "basins"))
        if surface_area <= 0:
            reg_missing.append(_missing(
                "Water-surface area on a basin",
                "Bather load and skimmer count size off the water-surface area.",
                field="basins"))
        if _flt(doc.get("design_flow_published_gpm")) <= 0:
            reg_missing.append(_missing(
                "Published design flow (GPM)",
                "The equipment/spec circulation flow, checked against the code minimum.",
                field="design_flow_published_gpm"))
        if not _rows(doc, "venue_fixtures"):
            reg_missing.append(_missing(
                "Fixtures (skimmers / main drains)",
                "The venue needs skimmers and VGB-compliant main drains for the schedule + entrapment check.",
                field="venue_fixtures"))
    add("regulated", "issue", reg_missing, n_a=not regulated)

    deliverable_missing = []
    segs = _rows(doc, "pipe_segments")
    if segs and not any(
        (getattr(s, "fittings_json", None) or getattr(s, "components_json", None)) for s in segs
    ):
        deliverable_missing.append(_missing(
            "Fittings / components on the pipe segments",
            "The DOC-0121 Fitting Schedule aggregates them; empty segments print an empty schedule.",
            field="pipe_segments"))
    n_blockers = sum(1 for i in issues if i["severity"] == BLOCKER)
    if n_blockers:
        deliverable_missing.append(_missing(
            f"Resolve {n_blockers} blocking issue{'s' if n_blockers != 1 else ''}",
            "Legal/safety blockers prevent Reviewed / Issued status and submission."))
    unacked = unacknowledged_warnings(doc, issues)
    if unacked:
        deliverable_missing.append(_missing(
            f"Acknowledge {len(unacked)} open warning{'s' if len(unacked) != 1 else ''}",
            "Each engineering-limit warning needs a recorded sign-off (who/when) to issue."))
    add("deliverables", "issue", deliverable_missing)

    # Gates run on MISSING INPUTS, not the blocked overlay: a blocker doesn't
    # un-compute the model (calc_ready stays true), it gates issuing — which the
    # deliverables section's "resolve N blockers" item + n_blockers enforce.
    by_key = {s["key"]: s for s in sections}
    calc_ready = not any(
        by_key[k]["missing"] for k in ("basin", "features", "tiers", "piping", "pump")
    )
    issue_ready = calc_ready and not any(s["missing"] for s in sections) and not n_blockers

    return {"sections": sections, "calc_ready": calc_ready, "issue_ready": issue_ready}


def summarize(issues):
    """Counts + the one-line summary for list views and headlines."""
    blockers = sum(1 for i in issues if i["severity"] == BLOCKER)
    warnings = sum(1 for i in issues if i["severity"] == WARNING)
    infos = sum(1 for i in issues if i["severity"] == INFO)
    parts = []
    if blockers:
        parts.append(f"{blockers} blocker{'s' if blockers != 1 else ''}")
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    if infos:
        parts.append(f"{infos} advisor{'ies' if infos != 1 else 'y'}")
    return {
        "blocker_count": blockers,
        "warning_count": warnings,
        "info_count": infos,
        "summary": ", ".join(parts) if parts else "No issues",
    }


# --------------------------------------------------------- fitting schedule


def _loads_list(text):
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def model_fitting_schedule(model):
    """The Fitting Schedule from a schema-2 model's network segments (``{key, qty}`` fittings and
    components), in the same row shape as :func:`fitting_schedule`."""
    totals = {}
    for seg in ((model or {}).get("network") or {}).get("segments") or []:
        material = seg.get("material") or "SCH40 PVC"
        size = seg.get("nominal_size") or ""
        for kind, field in (("Fitting", "fittings"), ("Component", "components")):
            for item in seg.get(field) or []:
                name = (item or {}).get("key") or (item or {}).get("type")
                if not name:
                    continue
                key = (kind, name, material, size)
                totals[key] = totals.get(key, 0) + int((item or {}).get("qty") or 1)
    return [
        {"kind": kind, "type": name, "material": material, "size": size, "qty": qty}
        for (kind, name, material, size), qty in sorted(totals.items())
    ]


def fitting_schedule(doc):
    """Aggregate the per-segment fittings/components JSON into the DOC-0121
    Fitting Schedule: total qty per (kind, type, material, size). A schema-2
    design (no pipe_segments rows, a ``model_json``) reads its model's segments."""
    if not _rows(doc, "pipe_segments") and doc.get("model_json"):
        raw = doc.get("model_json")
        try:
            model = raw if isinstance(raw, dict) else json.loads(raw)
        except (TypeError, ValueError):
            model = {}
        return model_fitting_schedule(model if isinstance(model, dict) else {})
    totals = {}
    default_material = doc.get("pipe_material") or "SCH40 PVC"
    for row in _rows(doc, "pipe_segments"):
        material = getattr(row, "material", None) or default_material
        size = getattr(row, "nominal_size", None) or ""
        for kind, field in (("Fitting", "fittings_json"), ("Component", "components_json")):
            for item in _loads_list(getattr(row, field, None)):
                name = item.get("type")
                if not name:
                    continue
                qty = int(item.get("qty") or 1)
                key = (kind, name, material, size)
                totals[key] = totals.get(key, 0) + qty
    return [
        {"kind": kind, "type": name, "material": material, "size": size, "qty": qty}
        for (kind, name, material, size), qty in sorted(totals.items())
    ]


# ------------------------------------------------------------ jinja methods
# (wired via hooks.py `jinja.methods` so the print Jinja sandbox — which cannot
# parse the per-row JSON — can render the Fitting Schedule and Design Review.)


def we_fitting_schedule(doc):
    return fitting_schedule(doc)


def we_design_issues(doc):
    """Open issues for the Design Review print section (persisted JSON first,
    rebuilt on the fly for docs saved before the issues fields existed)."""
    raw = doc.get("design_issues_json")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (ValueError, TypeError):
            pass
    try:
        return build_issues(doc)
    except Exception:
        return []


# =====================================================================================
# Schema 2: the Fountain Designer (``derive.derive_model``)
# =====================================================================================
#
# The model-2 producers. Every typed check from every phase becomes one ``DesignIssue``:
# model validation (``engine.model.validate`` Problems), the feature families and system
# modules (``engine.families`` Checks), the network, basin, surge and BOM checks of phase B,
# the structural checks of phase S, the electrical and controls notes and the NEC 680
# checklist of phase L, and the derive's own (stage failures, the Hazen-Williams
# cross-check, unpriced BOM lines). The dict is the v1 shape plus what the page reads
# (``public/js/fountain_designer/README.md``, "Issues"): ``step``, ``path``, ``remedies``
# and ``acknowledged``. Keys are ``code|scope`` with the scope a row id from the model
# (ids, unlike the old child-row names, survive every save), so an acknowledgement
# survives every derive that still finds the same thing.

MODEL_SECTIONS = [
    ("site", "Site"),
    ("features", "Features"),
    ("basin", "Basins"),
    ("piping", "Piping"),
    ("pump", "Pump"),
    ("control", "Control"),
    ("treatment", "Treatment"),
    ("lighting", "Lighting"),
    ("electrical", "Electrical"),
    ("winterization", "Winterization"),
    ("deliverables", "Package"),
]
MODEL_SECTION_LABELS = dict(MODEL_SECTIONS)

# Check sections that are not setup sections: (the section it counts under, the page step).
_SECTION_ALIASES = {
    "structure": ("basin", "basins"),
    "outputs": ("deliverables", "outputs"),
    "bypass": ("control", None),
    "surge": ("control", None),
    "regulated": ("treatment", None),
    "safety": ("pump", None),
    "project": ("site", None),
}

_PROBLEM_SECTIONS = (
    ("features", "features"), ("basins", "basin"), ("network.segments", "piping"), ("network.pumps", "pump"),
    ("network.terminals", "piping"), ("network.nodes", "piping"), ("equipment", "treatment"),
    ("lighting", "lighting"), ("electrical", "electrical"), ("setup", "features"), ("site", "site"),
)


def _index_of(rows, row_id):
    for i, r in enumerate(rows or []):
        if isinstance(r, dict) and r.get("id") == row_id:
            return i
    return None


def _ref_path(model, ref):
    """The model path the page focuses for a check's ``ref`` (a feature, basin, segment or pump id)."""
    if not ref:
        return None
    net = model.get("network") or {}
    i = _index_of(model.get("features"), ref)
    if i is not None:
        return f"features.{i}"
    i = _index_of(model.get("basins"), ref)
    if i is not None:
        return f"basins.{i}.depth_in"
    i = _index_of(net.get("segments"), ref)
    if i is not None:
        return f"network.segments.{i}.nominal_size"
    i = _index_of(net.get("pumps"), ref)
    if i is not None:
        return f"network.pumps.{i}.item"
    return None


def _remedies(model, code, ref, value):
    """One-click fixes the page can apply (``{label, set: {path: value}}``)."""
    out = []
    if code == "basin.freeboard" and isinstance(value, dict):
        i = _index_of(model.get("basins"), ref)
        if i is not None:
            b = model["basins"][i]
            depth = _flt(b.get("depth_in"))
            need = _flt(value.get("rise_in")) * 1.3 - _flt(value.get("freeboard_in"))
            if depth > 0 and need > 0:
                new = int(-(-(depth + need) // 1))
                out.append({"label": f"Deepen {b.get('label') or ref} to {new} in", "set": {f"basins.{i}.depth_in": new}})
    elif code == "IWF_SECONDARY":
        i = _index_of(model.get("features"), ref)
        sets = {"treatment.uv": True}
        if i is not None:
            sets[f"features.{i}.inputs.secondary_disinfection"] = True
        out.append({"label": "Add UV disinfection", "set": sets})
    return out


def _source_issue(model, check):
    """One check (any phase's shape) as a model-2 DesignIssue, or None for a pass."""
    severity = str(check.get("severity") or INFO).lower()
    if severity in ("pass", "ok"):
        return None
    if severity not in _SEVERITY_ORDER:
        severity = INFO
    code = str(check.get("code") or "CHECK")
    ref = check.get("ref") or check.get("feature") or check.get("basin") or ""
    if isinstance(ref, (list, tuple)):
        ref = ",".join(str(x) for x in ref)
    ref = str(ref or "")
    key = str(check.get("key") or (f"{code}|{ref}" if ref else code))[:250]
    section = str(check.get("section") or "features")
    step = None
    if section in _SECTION_ALIASES:
        section, step = _SECTION_ALIASES[section]
    return {
        "key": key,
        "code": code,
        "severity": severity,
        "title": str(check.get("title") or code)[:300],
        "detail": str(check.get("detail") or "")[:600],
        "section": section,
        "step": step,
        "path": _ref_path(model, ref),
        "ref": ref or None,
        "fix_hint": str(check.get("remedy") or "")[:600],
        "citation": str(check.get("citation") or "")[:600],
        "calc": str(check.get("_source") or ""),
        "remedies": _remedies(model, code, ref, check.get("value")),
        "acknowledged": None,
    }


def _problem_issue(problem):
    """An ``engine.model.validate`` Problem: a structural error blocks, a warning warns."""
    path = str(problem.get("path") or "")
    section = next((s for prefix, s in _PROBLEM_SECTIONS if path.startswith(prefix)), "features")
    code = f"MODEL_{str(problem.get('code') or 'problem').upper()}"
    return {
        "key": f"{code}|{path}"[:250], "code": code,
        "severity": BLOCKER if problem.get("severity") == "error" else WARNING,
        "title": str(problem.get("message") or code)[:300], "detail": "", "section": section, "step": None,
        "path": path.replace("[", ".").replace("]", "") or None, "ref": None, "fix_hint": "", "citation": "",
        "calc": "model", "remedies": [], "acknowledged": None,
    }


def _generic_issues(model):
    """A generic-estimate pick reaching a submittal (PLAN.md 3.2): a note in quick mode, a warning a
    designer signs off in full mode, where the design heads for a packet."""
    severity = WARNING if str(model.get("mode") or "quick") == "full" else INFO
    out = []
    for i, p in enumerate((model.get("network") or {}).get("pumps") or []):
        if not p.get("item") or str(p.get("catalog_source") or "").lower() == "generic estimate":
            out.append({
                "key": f"GENERIC_PUMP|{p.get('id')}", "code": "GENERIC_PUMP", "severity": severity,
                "title": f"Pump {p.get('id')} is a generic estimate, not a cataloged model",
                "detail": "Its curve is an estimate; a submittal needs the real pump and its datasheet curve.",
                "section": "pump", "step": None, "path": f"network.pumps.{i}.item", "ref": p.get("id"),
                "fix_hint": "Pick the pump model on the Pump room step.", "citation": "PLAN.md 3.2 (generic estimate flag)",
                "calc": "model", "remedies": [], "acknowledged": None,
            })
    for i, f in enumerate(model.get("features") or []):
        product = f.get("product") or {}
        if product.get("generic") and not product.get("item") and f.get("family") in ("jets", "foam", "laminar", "weir"):
            out.append({
                "key": f"GENERIC_PRODUCT|{f.get('id')}", "code": "GENERIC_PRODUCT", "severity": INFO,
                "title": f"{f.get('label') or f.get('id')}: generic nozzle or sheet product",
                "detail": "Sized from the family's generic figures; pick the product to use its published data.",
                "section": "features", "step": None, "path": f"features.{i}.product.item", "ref": f.get("id"),
                "fix_hint": "Pick the product on the Features step.", "citation": "", "calc": "model",
                "remedies": [], "acknowledged": None,
            })
    return out


def model_acks(model):
    """``{issue_key: {note, by, on}}`` from ``model.acks``."""
    acks = {}
    for a in model.get("acks") or []:
        if isinstance(a, dict) and a.get("issue_key"):
            acks[a["issue_key"]] = {"note": a.get("note") or "", "by": a.get("by") or "", "on": a.get("on") or ""}
    return acks


def model_issues(model, problems=None, checks=None):
    """Every typed issue for a derived model-2 design, most severe first, de-duplicated by key,
    with ``acknowledged`` filled from ``model.acks`` for warnings."""
    issues = [_problem_issue(p) for p in problems or []]
    for c in checks or []:
        i = _source_issue(model, c)
        if i:
            issues.append(i)
    issues += _generic_issues(model)
    acks = model_acks(model)
    seen, unique = set(), []
    for i in issues:
        if i["key"] in seen:
            continue
        seen.add(i["key"])
        if i["severity"] == WARNING and i["key"] in acks:
            i["acknowledged"] = acks[i["key"]]
        unique.append(i)
    unique.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 3))
    return unique


def open_warnings(issues):
    """Warnings nobody has signed off yet."""
    return [i for i in issues if i.get("severity") == WARNING and not i.get("acknowledged")]


def _basin_needed(features):
    return any(
        f.get("family") not in ("aerator", "mist", "pondless")
        and not (f.get("family") == "curtain" and (f.get("inputs") or {}).get("style") == "digital")
        for f in features
    )


def model_readiness(model, issues, flows=None, network=None):
    """Per-section readiness for a model-2 design (the page's ``readiness``): each section's
    ``status`` (``ok``, ``warn``, ``blocked``, ``missing``, ``n_a``) and ``missing[]`` strings, plus
    ``calc_ready`` (the engine had what it needs to size the design) and ``issue_ready`` (the
    DOC-0121 package gate: calc-ready, nothing missing, no blocker, every warning signed off)."""
    flows = flows or {}
    net = model.get("network") or {}
    features = model.get("features") or []
    basins = model.get("basins") or []
    missing = {key: [] for key, _ in MODEL_SECTIONS}
    n_a = set()

    if not str((model.get("site") or {}).get("address") or "").strip():
        missing["site"].append("Enter the site address")

    if not features:
        missing["features"].append("Add a feature")
    for f in features:
        flow = flows.get(f.get("id")) or {}
        if (flow.get("data") or {}).get("complete") is False or not flow.get("value"):
            missing["features"].append(f"Finish the inputs of {f.get('label') or f.get('id')}")

    if not basins and not _basin_needed(features):
        n_a.add("basin")
    elif not basins:
        missing["basin"].append("Add a basin")
    for b in basins:
        plan = _flt(b.get("volume_gal_override")) or _flt(b.get("diameter_in")) or (
            _flt(b.get("length_in")) * _flt(b.get("width_in"))) or _flt(b.get("surface_area_sqft"))
        if not plan or not (_flt(b.get("depth_in")) or _flt(b.get("volume_gal_override"))):
            missing["basin"].append(f"Give {b.get('label') or b.get('id')} its dimensions")

    if not net.get("pumps"):
        n_a.update({"piping", "pump", "control"})
        if any(f.get("node") for f in features):
            missing["pump"].append("Lay out the pump and pipes")
            n_a.discard("pump")
    else:
        for s in net.get("segments") or []:
            if not (s.get("nominal_size") or s.get("id_in")):
                missing["piping"].append(f"Size segment {s.get('label') or s.get('id')}")
        if not (network and network.get("pumps")):
            missing["pump"].append("Solve the network (a pump curve is needed)")
        if any(not p.get("item") for p in net.get("pumps") or []):
            missing["pump"].append("Pick a pump model")

    if not (model.get("electrical") or {}).get("service_v"):
        missing["electrical"].append("Enter the service voltage")
    if not (model.get("lighting") or {}).get("fixtures"):
        n_a.add("lighting")

    blockers = [i for i in issues if i.get("severity") == BLOCKER]
    unacked = open_warnings(issues)
    if blockers:
        missing["deliverables"].append(f"Resolve {len(blockers)} blocking issue{'s' if len(blockers) != 1 else ''}")
    if unacked:
        missing["deliverables"].append(f"Acknowledge {len(unacked)} open warning{'s' if len(unacked) != 1 else ''}")

    open_sev = {}
    for i in issues:
        if i.get("severity") == WARNING and i.get("acknowledged"):
            continue
        open_sev.setdefault(i.get("section"), set()).add(i.get("severity"))

    sections = []
    for key, label in MODEL_SECTIONS:
        sevs = open_sev.get(key, set())
        if key in n_a and not missing[key] and not sevs:
            status = "n_a"
        elif BLOCKER in sevs:
            status = "blocked"
        elif missing[key]:
            status = "missing"
        elif WARNING in sevs:
            status = "warn"
        else:
            status = "ok"
        sections.append({"key": key, "label": label, "status": status, "missing": list(dict.fromkeys(missing[key]))})

    calc_ready = not (missing["features"] or missing["basin"] or missing["piping"]
                      or [m for m in missing["pump"] if m != "Pick a pump model"])
    issue_ready = calc_ready and not blockers and not unacked and not any(
        missing[k] for k in missing if k != "deliverables")
    return {"sections": sections, "calc_ready": bool(calc_ready), "issue_ready": bool(issue_ready)}


def completion_from_readiness(readiness):
    """0-100: the share of applicable sections that are ``ok`` or ``warn``."""
    sections = [s for s in (readiness or {}).get("sections") or [] if s.get("status") != "n_a"]
    if not sections:
        return 0.0
    done = sum(1 for s in sections if s.get("status") in ("ok", "warn"))
    return round(done / len(sections) * 100.0, 1)


def model_verdict(issues, readiness):
    """The verdict the page's panel leads with: ``bad`` with any blocker, ``warn`` with open
    warnings or an unsized design, else ``good``. ``package_ready`` = no blocker and every warning
    signed off (the README contract)."""
    blockers = [i for i in issues if i.get("severity") == BLOCKER]
    unacked = open_warnings(issues)
    if blockers:
        n = len(blockers)
        level = "bad"
        headline = f"Does not work yet: {n} blocking issue{'s' if n != 1 else ''}. {blockers[0]['title']}"
    elif not (readiness or {}).get("calc_ready"):
        level = "warn"
        headline = "Not sized yet: finish the inputs the readiness list names."
    elif unacked:
        n = len(unacked)
        level = "warn"
        headline = f"Works, with {n} warning{'s' if n != 1 else ''} to sign off."
    else:
        level = "good"
        headline = "Works. Ready to issue." if (readiness or {}).get("issue_ready") else "Works. Good to quote."
    return {"level": level, "headline": headline[:240], "package_ready": not blockers and not unacked}
