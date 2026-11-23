"""Foam (aerating) jets and geysers.

Industry report section 1.3. A foam jet sizes like any jet (:mod:`.jets`: height from the
nozzle table), with one input that matters more than the rest: **water depth over the
nozzle**. Injector types (OASE Cascade, Geyser; Crystal WMO) are level-sensitive: below
normal level the column is slender and taller, above it fuller and lower. No public source
gives the band in inches, so the cut sheet's band wins and the Sapphire house rule
(``foam_submergence_min_in`` / ``_max_in``) fills in until one is picked.

``derived`` keys read by :func:`checks`: those of :func:`.jets.checks`.
"""

from __future__ import annotations

from typing import Any

from .. import rules as R
from . import jets
from .base import (
    INFO,
    WARNING,
    Check,
    FamilyResult,
    feature_dict,
    flow_data,
    num,
    product_of,
    spec,
    values,
)

FAMILY = "foam"
LABEL = "Foam jet or geyser"
TYPES = ("Foam Jet", "Geyser", "Aerating Jet", "Cascade Jet")

CIT_LEVEL = (
    "https://www.oase.com/cs-CZ/fountain-technology/cascade-50-t-npt (OASE Cascade: level-sensitive); "
    "https://www.pool360.com/Product/1-wmo-series-shallow-water-foam-jet-pool-cft-27-210 (Crystal WMO: level-dependent)"
)
CIT_CHECK_VALVE = "Industry report section 1.3: a check valve per level-sensitive jet, or drain-down to injector height at shutdown"


def inputs() -> list[dict[str, Any]]:
    base = [r for r in jets.inputs() if r["name"] not in ("nozzle_type", "angle_deg")]
    return [
        *base,
        spec("depth_over_nozzle_in", "Water over the nozzle", "number", "in", None, True,
             "At normal operating level."),
        spec("level_sensitive", "Level-sensitive nozzle", "bool", "", True, False,
             "Injector types are; a Schaumsprudler-style nozzle is not."),
        spec("check_valve", "Check valve on each jet", "bool", "", False),
        spec("drains_down_ok", "Drain-down to injector height is acceptable", "bool", "", False, False,
             "Without a check valve the basin falls to the injector at shutdown."),
        spec("autofill_deadband_in", "Autofill deadband", "number", "in", None, False,
             "Blank uses the house rule."),
    ]


def band(feature: Any, rules: dict[str, Any] | None = None) -> tuple[float, float, str]:
    """The submergence band (min, max, citation): the cut sheet's, else the house rule."""
    profile = product_of(feature).get("profile") or {}
    lo, hi = num(profile.get("min_depth_in")), num(profile.get("max_depth_in"))
    if lo > 0 and hi > lo:
        return lo, hi, profile.get("source_url") or "nozzle cut sheet"
    return (
        R.rule(rules, "foam_submergence_min_in"),
        R.rule(rules, "foam_submergence_max_in"),
        R.citation("foam_submergence_min_in", rules),
    )


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    f = dict(feature_dict(feature))
    f["inputs"] = dict(f.get("inputs") or {}, nozzle_type="aerating")
    res = jets.flow(f, site, rules)
    lo, hi, cit = band(feature, rules)
    v = values(feature, inputs())
    res.calc = "foam_family_flow"
    res.data["band_in"] = (lo, hi)
    res.data["depth_over_nozzle_in"] = num(v.get("depth_over_nozzle_in"))
    res.steps.append(f"submergence band {lo:g}-{hi:g} in ({cit})")
    res.citations += [CIT_LEVEL, cit]
    return res


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    out = jets.jet_checks(fid, v, d, derived, site, rules, prefix="FOAM")
    lo, hi, cit = band(feature, rules)
    from_house = cit == R.citation("foam_submergence_min_in", rules)
    if from_house:
        out.append(Check(
            "FOAM_BAND_HOUSE_RULE", INFO, "Submergence band is the house rule, not the cut sheet",
            detail=f"Using {lo:g}-{hi:g} in. No public source gives this figure.",
            remedy="Pick the nozzle and enter its min and max depth from the cut sheet.",
            citation=cit, feature=fid,
        ))
    depth = num(v.get("depth_over_nozzle_in"))
    if v.get("level_sensitive") and depth > 0 and not (lo <= depth <= hi):
        out.append(Check(
            "FOAM_DEPTH_OUT_OF_BAND", WARNING, "Water over the foam jet is outside its band",
            detail=f"{depth:g} in over the nozzle; it works between {lo:g} and {hi:g} in.",
            remedy="Set the nozzle height (or the overflow) so normal level sits mid-band.",
            citation=cit, section="basin", feature=fid, value=depth, limit=(lo, hi), unit="in",
        ))
    if v.get("level_sensitive") and not v.get("check_valve") and not v.get("drains_down_ok"):
        out.append(Check(
            "FOAM_NO_CHECK_VALVE", WARNING, "Level-sensitive jet has no check valve",
            detail="At shutdown the basin drains back through the jet down to injector height.",
            remedy="Fit a check valve on each jet, or confirm the drain-down is acceptable.",
            citation=CIT_CHECK_VALVE, section="piping", feature=fid,
        ))
    deadband = num(v.get("autofill_deadband_in")) or R.rule(rules, "autofill_deadband_in")
    if v.get("level_sensitive") and deadband > (hi - lo):
        out.append(Check(
            "FOAM_AUTOFILL_BAND", WARNING, "Autofill lets the level drift past the jet's band",
            detail=f"Deadband {deadband:g} in against a {hi - lo:g} in band.",
            remedy="Use an electronic level sensor with a tighter deadband.",
            citation=R.citation("autofill_deadband_in", rules), section="basin", feature=fid,
            value=deadband, limit=hi - lo, unit="in",
        ))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    return jets.jet_terminal(feature, d)
