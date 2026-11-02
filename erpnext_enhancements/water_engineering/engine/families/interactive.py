"""Interactive dry-deck plazas and splash pads (sequenced valves).

Industry report section 1.5. Two jobs:

1. **Size the feature pump to the worst sequence step**, not the sum of every feature. The
   sequence is a list of steps, each the labels of the features open at once. The saving
   over "everything on" is where automation pays (Vortex quotes 30-50%).
2. **Utah R392-302-41 as a pass/fail list.** Every item is a :class:`.base.Check` with
   severity ``pass`` or, when it fails, ``blocker`` where the site's state regulates
   interactive features with a rule like Utah's (``regulation.iwf_regulated`` and
   ``iwf_like_utah`` in the jurisdiction table, phase J) and ``warning`` elsewhere, cited with
   the state's ``iwf_cite``. A state with its own rule gets an ``info`` carrying the table's
   notes; a site with no jurisdiction data gets one saying so. An item the designer has not
   answered yet is ``info``, never a silent pass.

``derived`` keys read by :func:`checks`: ``flow``, ``basin`` (the collector tank, for its
volume when ``tank_gal`` is blank) and ``tank_gal``.
"""

from __future__ import annotations

import math
from typing import Any

from .. import jurisdiction as juris
from ..basin import basin_volume
from ..envelope import make_input
from .base import (
    BLOCKER,
    INFO,
    PASS,
    WARNING,
    Check,
    FamilyResult,
    feature_dict,
    flow_data,
    num,
    spec,
    values,
)

FAMILY = "interactive"
LABEL = "Interactive plaza or splash pad"
TYPES = ("Splash Pad", "Interactive Plaza", "Dry Deck", "Interactive Fountain")

CIT_UT = "https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41"
CIT_MAHC = "https://stacks.cdc.gov/view/cdc/153389/cdc_153389_DS1.pdf (MAHC: feature-to-filter flow <= 3:1; secondary disinfection after the feature pump) [snippet]"
CIT_SEQ = "https://www.vortex-intl.com/blog/how-to-design-low-impact-splashpad (sequencing saves 30-50% of total GPM)"
CIT_LINE = "https://www.vortex-intl.com/blog/water-play-design-mistakes-to-avoid/ (<= 30 ft manifold to farthest feature) [snippet]"

TURNOVER_MIN = 30.0
MIN_INLETS = 4
TANK_MINUTES = 3.0
MAX_VELOCITY_FPS = 20.0
MAX_OPENING_IN = 0.5
MIN_DECK_FT = 3.0
MIN_SLOPE_PCT = 2.0
MIN_NIGHT_FC = 5.0
MAX_FEATURE_TO_FILTER = 3.0
MAX_LINE_FT = 30.0

FEATURE_COLUMNS = [
    {"name": "label", "label": "Feature", "kind": "text"},
    {"name": "gpm", "label": "Flow each", "kind": "number", "unit": "gpm"},
    {"name": "count", "label": "Count", "kind": "integer"},
    {"name": "orifice_in", "label": "Orifice", "kind": "number", "unit": "in"},
    {"name": "opening_in", "label": "Ground opening", "kind": "number", "unit": "in"},
    {"name": "line_ft", "label": "Manifold to feature", "kind": "number", "unit": "ft"},
    {"name": "fogger", "label": "Fogger", "kind": "bool"},
]


def inputs() -> list[dict[str, Any]]:
    return [
        spec("play_features", "Play features", "table", "", [], True,
             "Each spray, post, tent or fogger: flow each and count.", columns=FEATURE_COLUMNS),
        spec("sequence", "Sequence steps", "table", "", [], False,
             "Each step lists the features open together. Blank means everything runs at once.",
             columns=[{"name": "open", "label": "Open features", "kind": "text"}]),
        spec("system_volume_gal", "System volume (tank + lines)", "number", "gal", None, False),
        spec("tank_gal", "Collector tank", "number", "gal", None, False, "Blank uses the tank basin."),
        spec("filter_gpm", "Filter pump flow", "number", "gpm", None, False, "Blank: sized to the 30-minute turnover."),
        spec("inlet_count", "Treated-water inlets", "integer", "", None, False),
        spec("deck_width_ft", "Continuous deck width", "number", "ft", None, False),
        spec("deck_slope_pct", "Ground slope away from the pad", "number", "%", None, False),
        spec("drain_passes_half_inch", "Drain grates stop a 1/2 in sphere", "bool", "", None),
        spec("orp_ph_controller", "Automatic ORP and pH controller", "bool", "", None),
        spec("secondary_disinfection", "Secondary disinfection (UV or ozone)", "bool", "", None),
        spec("separate_loop", "Treatment loop separate from the feature pump", "bool", "", None),
        spec("level_controller", "Automatic level controller", "bool", "", None),
        spec("fogger_potable", "Foggers fed from potable water", "bool", "", None),
        spec("night_fc", "Night lighting on the pad", "number", "fc", None),
    ]


def _rows(v: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, r in enumerate(v.get("play_features") or []):
        r = dict(r or {})
        r["label"] = str(r.get("label") or f"feature {i + 1}")
        r["gpm"] = num(r.get("gpm"))
        r["count"] = max(int(num(r.get("count"), 1)), 1)
        rows.append(r)
    return rows


def _step_labels(step: Any) -> list[str]:
    if isinstance(step, dict):
        step = step.get("open") or []
    if isinstance(step, str):
        step = [s.strip() for s in step.split(",")]
    return [str(s) for s in step or [] if str(s).strip()]


def sequence_demand(rows: list[dict[str, Any]], sequence: list[Any]) -> dict[str, Any]:
    """Flow of every sequence step; the worst one sizes the feature pump."""
    by_label = {r["label"]: r["gpm"] * r["count"] for r in rows}
    all_on = sum(by_label.values())
    steps = []
    unknown: list[str] = []
    for i, step in enumerate(sequence or []):
        labels = _step_labels(step)
        unknown += [lb for lb in labels if lb not in by_label]
        steps.append({"step": i + 1, "open": labels, "gpm": sum(by_label.get(lb, 0.0) for lb in labels)})
    if not steps:
        return {"all_on_gpm": all_on, "worst_gpm": all_on, "worst_step": None, "steps": [], "unknown": []}
    worst = max(steps, key=lambda s: s["gpm"])
    return {"all_on_gpm": all_on, "worst_gpm": worst["gpm"], "worst_step": worst["step"], "steps": steps,
            "unknown": sorted(set(unknown))}


def tank_required_gal(feature_pump_gpm: float, filter_gpm: float) -> float:
    """Utah: the collector tank holds >= 3 minutes of the combined pump flow."""
    return TANK_MINUTES * (max(feature_pump_gpm, 0.0) + max(filter_gpm, 0.0))


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    rows = _rows(v)
    if not rows:
        return FamilyResult(calc="interactive_family_flow", unit="GPM", citations=[CIT_UT],
                            warnings=["List the play features and their flows."], data={"complete": False})
    seq = sequence_demand(rows, v.get("sequence") or [])
    volume = num(v.get("system_volume_gal"))
    turnover_gpm = volume / TURNOVER_MIN if volume else 0.0
    filter_gpm = num(v.get("filter_gpm")) or turnover_gpm
    tank = tank_required_gal(seq["worst_gpm"], filter_gpm)
    steps = []
    if seq["steps"]:
        for s in seq["steps"]:
            steps.append(f"step {s['step']}: {', '.join(s['open']) or '(none)'} = {s['gpm']:.1f} gpm")
        saving = 1 - seq["worst_gpm"] / seq["all_on_gpm"] if seq["all_on_gpm"] else 0.0
        steps.append(
            f"feature pump = worst step {seq['worst_step']} = {seq['worst_gpm']:.1f} gpm "
            f"(all on {seq['all_on_gpm']:.1f} gpm; sequencing saves {saving:.0%})"
        )
    else:
        steps.append(f"no sequence: feature pump = all features on = {seq['all_on_gpm']:.1f} gpm")
    if volume:
        steps.append(f"turnover: {volume:g} gal / {TURNOVER_MIN:g} min = {turnover_gpm:.1f} gpm minimum filter flow")
    steps.append(f"tank >= {TANK_MINUTES:g} min x ({seq['worst_gpm']:.1f} + {filter_gpm:.1f}) gpm = {tank:.0f} gal")
    warnings = []
    if not seq["steps"]:
        warnings.append("No sequence entered: the feature pump is sized for every feature at once.")
    if seq["unknown"]:
        warnings.append(f"Sequence names features that are not listed: {', '.join(seq['unknown'])}.")
    data = {
        "gpm": seq["worst_gpm"],
        "feature_pump_gpm": seq["worst_gpm"],
        "all_on_gpm": seq["all_on_gpm"],
        "worst_step": seq["worst_step"],
        "steps": seq["steps"],
        "turnover_gpm": turnover_gpm,
        "filter_gpm": filter_gpm,
        "tank_required_gal": tank,
        "rows": rows,
        "complete": True,
    }
    return FamilyResult(
        calc="interactive_family_flow",
        value=seq["worst_gpm"],
        unit="GPM",
        inputs={
            "feature_count": make_input(sum(r["count"] for r in rows), "", "user"),
            "sequence_steps": make_input(len(seq["steps"]), "", "user"),
            "system_volume_gal": make_input(volume, "gal", "user"),
        },
        formula="feature pump = max over steps of sum(gpm x count of open features); filter >= V / 30 min; tank >= 3 min x (feature + filter)",
        steps=steps,
        citations=[CIT_UT, CIT_SEQ],
        warnings=warnings,
        data=data,
    )


def _item(code: str, title: str, ok: bool | None, fail_sev: str, *, detail: str = "", remedy: str = "",
          section: str = "features", citation: str = CIT_UT, fid: str = "", value: Any = None,
          limit: Any = None, unit: str = "") -> Check:
    if ok is None:
        sev, detail = INFO, (detail or "Not answered yet.")
    else:
        sev = PASS if ok else fail_sev
    return Check(code, sev, title, section=section, detail=detail, remedy="" if ok else remedy,
                 citation=citation, feature=fid, value=value, limit=limit, unit=unit)


def _yes(v: dict[str, Any], key: str) -> bool | None:
    val = v.get(key)
    return None if val is None or val == "" else bool(val)


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    if not d.get("complete"):
        return [Check("IWF_INCOMPLETE", INFO, "List the play features to run the Utah checklist", feature=fid)]
    entry = juris.entry_for(site)
    reg = juris.regulation(entry)
    fail = BLOCKER if juris.iwf_like_utah(entry) else WARNING
    out = _checklist(v, d, derived, fail, fid)
    cite = str(reg.get("iwf_cite") or "") if juris.iwf_regulated(entry) else ""
    if cite:  # the state's own citation for the lines its rule makes
        for c in out:
            if c.citation.startswith(CIT_UT):
                c.citation = cite + c.citation[len(CIT_UT):]
    return _state_note(site, entry, fid) + out


def _state_note(site: dict[str, Any] | None, entry: dict[str, Any] | None, fid: str) -> list[Check]:
    """An INFO when the Utah checklist is advice here: the state has its own rule, regulates none, or has no row."""
    if entry is None:
        return [Check("IWF_NO_JURISDICTION", INFO, "No jurisdiction data: the Utah checklist is shown as advice",
                      detail=juris.no_data_warning(site), remedy="Pick the site's state on the Site step.",
                      section="site", citation=CIT_UT, feature=fid)]
    if juris.iwf_like_utah(entry):
        return []
    reg = juris.regulation(entry)
    state = juris.state_name(site, entry)
    if juris.iwf_regulated(entry):
        title = f"{state} regulates interactive features under its own rule" + (f" ({reg['iwf_rule']})" if reg.get("iwf_rule") else "")
    elif reg.get("iwf_regulated") is False:
        title = f"{state} does not regulate interactive features as pools"
    else:  # null in the table: the research could not pin the rule down (its cite says why)
        title = f"{state}'s rule for interactive features is not on file"
    return [Check("IWF_STATE_RULES", INFO, title,
                  detail=(str(reg.get("notes") or "") + " The Utah R392-302-41 lines below are shown as advice.").strip(),
                  remedy="Check the state's rule and the local health department before plan review.", section="site",
                  citation=str(reg.get("iwf_cite") or reg.get("pool_cite") or ""), feature=fid)]


def _checklist(v: dict[str, Any], d: dict[str, Any], derived: dict[str, Any] | None, fail: str, fid: str) -> list[Check]:
    rows = d["rows"]
    out: list[Check] = []

    volume = num(v.get("system_volume_gal"))
    filt = num(v.get("filter_gpm"))
    if volume and filt:
        turnover = volume / filt
        out.append(_item("IWF_TURNOVER", "Whole volume treated within 30 minutes", turnover <= TURNOVER_MIN + 1e-9, fail,
                         detail=f"{volume:g} gal / {filt:g} gpm = {turnover:.1f} min.",
                         remedy="Raise the filter flow or cut the system volume.", section="treatment", fid=fid,
                         value=round(turnover, 1), limit=TURNOVER_MIN, unit="min"))
    else:
        out.append(_item("IWF_TURNOVER", "Whole volume treated within 30 minutes", None, fail, section="treatment",
                         detail="Enter the system volume and filter flow.", fid=fid))

    inlets = v.get("inlet_count")
    out.append(_item("IWF_INLETS", "At least 4 equally spaced inlets",
                     None if inlets in (None, "") else int(num(inlets)) >= MIN_INLETS, fail,
                     detail=f"{int(num(inlets))} inlets." if inlets not in (None, "") else "",
                     remedy="Add inlets to reach 4.", section="treatment", fid=fid,
                     value=None if inlets in (None, "") else int(num(inlets)), limit=MIN_INLETS))

    tank = num(v.get("tank_gal")) or num((derived or {}).get("tank_gal"))
    if not tank and derived and derived.get("basin"):
        b = derived["basin"]
        tank = num(b.get("volume_gal_override")) or (basin_volume(
            shape="Cylindrical" if str(b.get("shape") or "").lower().startswith(("cyl", "round", "circ")) else "Rectangular",
            length_in=num(b.get("length_in")), width_in=num(b.get("width_in")),
            height_in=num(b.get("depth_in")), diameter_in=num(b.get("diameter_in")),
        ).value or 0.0)
    need = d["tank_required_gal"]
    out.append(_item("IWF_TANK", "Collector tank holds 3 minutes of combined pump flow",
                     None if not tank else tank >= need - 1e-9, fail,
                     detail=(f"{tank:.0f} gal; needs {need:.0f} gal." if tank else f"Needs {need:.0f} gal; enter the tank."),
                     remedy="Enlarge the collector tank.", section="basin", fid=fid,
                     value=round(tank) if tank else None, limit=round(need), unit="gal"))

    fast = []
    for r in rows:
        orifice = num(r.get("orifice_in"))
        if orifice > 0 and r["gpm"] > 0:
            vel = r["gpm"] / 448.831 / (math.pi / 4 * (orifice / 12.0) ** 2)
            if vel > MAX_VELOCITY_FPS:
                fast.append(f"{r['label']} {vel:.1f} ft/s")
    has_orifices = any(num(r.get("orifice_in")) > 0 for r in rows)
    out.append(_item("IWF_VELOCITY", "Nozzle velocity 20 ft/s or less", None if not has_orifices else not fast, fail,
                     detail="; ".join(fast) if fast else ("" if has_orifices else "Enter orifice sizes."),
                     remedy="Use larger orifices or less flow on those features.", fid=fid,
                     limit=MAX_VELOCITY_FPS, unit="ft/s"))

    openings = [num(r.get("opening_in")) for r in rows if num(r.get("opening_in")) > 0]
    big = [r["label"] for r in rows if num(r.get("opening_in")) > MAX_OPENING_IN]
    out.append(_item("IWF_OPENINGS", "Ground-level openings 1/2 in or less", None if not openings else not big, fail,
                     detail=", ".join(big), remedy="Use nozzle caps with openings of 1/2 in or less.", fid=fid,
                     value=max(openings) if openings else None, limit=MAX_OPENING_IN, unit="in"))
    out.append(_item("IWF_DRAIN_SPHERE", "Drains do not pass a 1/2 in sphere", _yes(v, "drain_passes_half_inch"), fail,
                     remedy="Use drain grates that stop a 1/2 in sphere.", section="basin", fid=fid))

    deck = v.get("deck_width_ft")
    out.append(_item("IWF_DECK", "Continuous deck at least 3 ft wide",
                     None if deck in (None, "") else num(deck) >= MIN_DECK_FT, fail,
                     remedy="Widen the deck to 3 ft.", section="site", fid=fid,
                     value=None if deck in (None, "") else num(deck), limit=MIN_DECK_FT, unit="ft"))
    slope = v.get("deck_slope_pct")
    out.append(_item("IWF_SLOPE", "Ground sloped away at 2% or more",
                     None if slope in (None, "") else num(slope) >= MIN_SLOPE_PCT, fail,
                     remedy="Grade the surround to fall away at 2%.", section="site", fid=fid,
                     value=None if slope in (None, "") else num(slope), limit=MIN_SLOPE_PCT, unit="%"))

    out.append(_item("IWF_ORP_PH", "Automatic ORP and pH controller", _yes(v, "orp_ph_controller"), fail,
                     remedy="Add an automatic ORP/pH controller.", section="treatment", fid=fid))
    out.append(_item("IWF_SECONDARY", "Secondary disinfection (UV or ozone)", _yes(v, "secondary_disinfection"), fail,
                     remedy="Add UV or ozone after the feature pump.", section="treatment", fid=fid,
                     citation=CIT_UT + "; " + CIT_MAHC))
    out.append(_item("IWF_LOOP", "Treatment on a loop separate from the feature pump", _yes(v, "separate_loop"), fail,
                     remedy="Give the filter and sanitizer their own circulation pump.", section="treatment", fid=fid))
    out.append(_item("IWF_LEVEL", "Automatic level controller", _yes(v, "level_controller"), fail,
                     remedy="Add an automatic level controller on the tank.", section="basin", fid=fid))
    if any(r.get("fogger") for r in rows):
        out.append(_item("IWF_FOGGER", "Foggers fed from potable water", _yes(v, "fogger_potable"), fail,
                         remedy="Feed the foggers from the potable supply, never the tank.", fid=fid))
    fc = v.get("night_fc")
    out.append(_item("IWF_LIGHT", "At least 5 fc of light at night",
                     None if fc in (None, "") else num(fc) >= MIN_NIGHT_FC, fail,
                     remedy="Add site lighting to 5 fc on the pad.", section="lighting", fid=fid,
                     value=None if fc in (None, "") else num(fc), limit=MIN_NIGHT_FC, unit="fc"))

    filter_flow = d["filter_gpm"]
    if filter_flow > 0:
        ratio = d["feature_pump_gpm"] / filter_flow
        out.append(_item("IWF_FEATURE_FILTER_RATIO", "Feature-to-filter flow 3:1 or less", ratio <= MAX_FEATURE_TO_FILTER + 1e-9,
                         WARNING, detail=f"{d['feature_pump_gpm']:.0f} : {filter_flow:.0f} gpm = {ratio:.2f}:1.",
                         remedy="Raise the filter flow or cut the worst sequence step.", section="treatment",
                         citation=CIT_MAHC, fid=fid, value=round(ratio, 2), limit=MAX_FEATURE_TO_FILTER))

    long_lines = [r["label"] for r in rows if num(r.get("line_ft")) > MAX_LINE_FT]
    if long_lines:
        out.append(Check("IWF_LINE_LENGTH", INFO, "Feature lines longer than 30 ft",
                         detail=", ".join(long_lines) + ". Vortex sizes lines assuming 30 ft or less.",
                         remedy="Check those features' head at the far end.", section="piping",
                         citation=CIT_LINE, feature=fid))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    """A valve manifold: one takeoff per play feature, plus the sequence so the solver can
    run the worst step."""
    d = (derived or {}).get("flow") or flow(feature).data
    fid = feature_dict(feature).get("id") or "f"
    takeoffs = []
    for r in d.get("rows") or []:
        for i in range(r["count"]):
            takeoffs.append({"id": f"{fid}-{r['label']}-{i + 1}", "label": r["label"], "flow_gpm": r["gpm"]})
    return {
        "kind": "manifold",
        "params": {"takeoffs": takeoffs, "sequence": d.get("steps") or [], "design_flow_gpm": d.get("gpm")},
    }
