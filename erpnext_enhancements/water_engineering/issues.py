# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Typed design issues, readiness and the verdict for a Water Feature Design.

The single producer of the typed structures the page, the Desk form, the list view, the prints and
the AI tools read off a schema-2 design (``derive.derive_model`` calls it on every derive):

* ``model_issues(model, problems, checks)`` — a flat list of ``DesignIssue`` dicts, severity-coded
  (``blocker`` = legal, safety or physical failure, ``warning`` = an engineering limit, ``info`` =
  advisory), section- and step-keyed, addressed by a model path so the page can jump to the row,
  each with a fix hint, remedies and its citation.
* ``model_readiness(model, issues)`` — per-section completeness with two gates: ``calc_ready``
  (everything the hydraulics need) and ``issue_ready`` (the DOC-0121 package gate).
* ``model_verdict(issues, readiness)`` — the good / warn / bad verdict the page's right panel shows.

``summarize`` gives the counts for the list view, ``fitting_schedule`` the DOC-0121 Fitting Schedule
from the model's segments, and ``we_design_issues`` the stored issues for a print.

The schema-1 producers (``build_issues`` and ``build_readiness``, which read the old child rows and
calc audit rows) went with those child DocTypes in v1.771.0 (phase H of the rebuild, ADR 0018).

This module deliberately imports no frappe (stub-testable like the engine).
"""

from __future__ import annotations

import json

BLOCKER = "blocker"
WARNING = "warning"
INFO = "info"

_SEVERITY_ORDER = {BLOCKER: 0, WARNING: 1, INFO: 2}


def _flt(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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


def calc_error_issue(detail=""):
    """The blocker recorded when recompute itself fails."""
    return _issue(
        "CALC_ERROR", BLOCKER, "Calculation error — the engine could not run this design",
        "project", detail=detail or "See the Error Log.",
        fix_hint="Fix the inputs the Error Log points at, then save again.",
    )


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
    """The DOC-0121 Fitting Schedule of a design, from its ``model_json`` network segments."""
    raw = doc.get("model_json")
    try:
        model = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        model = {}
    return model_fitting_schedule(model if isinstance(model, dict) else {})


# ------------------------------------------------------------ jinja method
# (wired via hooks.py `jinja.methods`: the Design Review section of a print reads the stored issues.)


def we_design_issues(doc):
    """The design's open issues for a print, from the stored ``design_issues_json``; [] when there
    are none or the field cannot be read."""
    raw = doc.get("design_issues_json")
    if not raw:
        return []
    try:
        data = raw if isinstance(raw, list) else json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


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
