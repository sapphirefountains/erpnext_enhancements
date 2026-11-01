"""High-pressure misting and fog.

Industry report section 1.9. Nozzle count x gpm per nozzle sizes the pump; the line must be
rated for the operating pressure (about 1,000 psi). The sources are patio-cooling suppliers,
a weak fit for fountains, so the numbers are a starting point. Utah requires fogger water to
come from a potable source, never the feature's reservoir, so a mist system is its own
once-through loop and not a terminal on the fountain's network.

``derived`` keys read by :func:`checks`: ``flow``.
"""

from __future__ import annotations

from typing import Any

from ..envelope import make_input
from .base import (
    BLOCKER,
    INFO,
    WARNING,
    Check,
    FamilyResult,
    count_of,
    feature_dict,
    flow_data,
    num,
    spec,
    values,
)

FAMILY = "mist"
LABEL = "Mist or fog"
TYPES = ("Mist", "Fog", "Fogger")

CIT_NOZZLE = (
    "https://www.advancedmistingsystems.com/stainless-steel-10-24-anti-drip-cleanable-nozzle/ "
    "(0.008 in ~0.020 gpm, 0.012 in ~0.028 gpm at ~1,000 psi; 0.012 in for dry climates) [weak fit]"
)
CIT_PUMP = "https://www.cool-off.com/high-pressure-misting-system-pumps (pump sized by nozzle count) [weak fit]"
CIT_POTABLE = "https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41 (foggers fed with potable water)"

ORIFICE_GPM = {"0.008": 0.020, "0.012": 0.028}
DEFAULT_PSI = 1000.0


def inputs() -> list[dict[str, Any]]:
    return [
        spec("nozzle_count", "Nozzles", "integer", "", None, True),
        spec("orifice_in", "Orifice", "select", "in", "0.012", False, "0.012 in suits a dry climate.", list(ORIFICE_GPM)),
        spec("gpm_each", "Flow per nozzle", "number", "gpm", None, False, "From the cut sheet; overrides the orifice figure."),
        spec("pressure_psi", "Operating pressure", "number", "psi", DEFAULT_PSI),
        spec("supply", "Water supply", "select", "", "potable", False, "Utah requires potable water.",
             ["potable", "reservoir"]),
        spec("line_rating_psi", "Mist line pressure rating", "number", "psi", None),
    ]


def flow(feature: Any, site: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    v = values(feature, inputs())
    zones = count_of(feature)
    nozzles = int(num(v.get("nozzle_count")))
    orifice = str(v.get("orifice_in") or "0.012")
    each = num(v.get("gpm_each")) or ORIFICE_GPM.get(orifice, ORIFICE_GPM["0.012"])
    if nozzles <= 0:
        return FamilyResult(calc="mist_family_flow", unit="GPM", warnings=["Enter the nozzle count."],
                            data={"complete": False})
    total = nozzles * each * zones
    psi = num(v.get("pressure_psi"), DEFAULT_PSI)
    return FamilyResult(
        calc="mist_family_flow",
        value=total,
        unit="GPM",
        inputs={"nozzle_count": make_input(nozzles, "", "user"), "gpm_each": make_input(each, "gpm", "lookup"),
                "pressure_psi": make_input(psi, "psi", "user")},
        formula="Q = nozzles x gpm per nozzle",
        steps=[f"{nozzles} nozzles x {each:.3f} gpm x {zones} = {total:.2f} gpm at {psi:g} psi"],
        citations=[CIT_NOZZLE, CIT_PUMP],
        data={"gpm": total, "gpm_each": each, "nozzles": nozzles, "pressure_psi": psi, "orifice_in": orifice,
              "complete": True},
    )


def checks(feature: Any, derived: dict[str, Any] | None = None, site: dict[str, Any] | None = None,
           rules: dict[str, Any] | None = None) -> list[Check]:
    v = values(feature, inputs())
    fid = feature_dict(feature).get("id") or ""
    d = flow_data(flow, feature, derived, site, rules)
    if not d.get("complete"):
        return [Check("MIST_INCOMPLETE", INFO, "Enter the nozzle count", feature=fid)]
    out: list[Check] = []
    if str(v.get("supply") or "potable") != "potable":
        utah = (site or {}).get("jurisdiction", "UT") == "UT"
        out.append(Check(
            "MIST_NOT_POTABLE", BLOCKER if utah else WARNING, "Mist is fed from the reservoir, not potable water",
            detail="Fog drifts onto people; Utah requires a potable supply.",
            remedy="Feed the mist pump from the potable supply through a backflow preventer.",
            citation=CIT_POTABLE, section="treatment", feature=fid,
        ))
    rating = num(v.get("line_rating_psi"))
    if rating > 0 and rating < d["pressure_psi"]:
        out.append(Check(
            "MIST_LINE_RATING", BLOCKER, "Mist line is rated below the operating pressure",
            detail=f"Line {rating:g} psi; the pump runs at {d['pressure_psi']:g} psi.",
            remedy="Use high-pressure line rated above the pump's pressure.", section="piping", feature=fid,
            value=rating, limit=d["pressure_psi"], unit="psi",
        ))
    elif rating <= 0:
        out.append(Check("MIST_LINE_RATING_UNSET", INFO, "Enter the mist line's pressure rating",
                         section="piping", feature=fid))
    if d["orifice_in"] == "0.008" and not num(v.get("gpm_each")):
        out.append(Check("MIST_DRY_CLIMATE", INFO, "Consider 0.012 in nozzles in Utah's dry climate",
                         citation=CIT_NOZZLE, feature=fid))
    return out


def terminal(feature: Any, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    d = (derived or {}).get("flow") or flow(feature).data
    return {"kind": "fixed_flow", "params": {"flow_gpm": d.get("gpm"), "psi": d.get("pressure_psi"),
                                             "separate_system": True, "supply": "potable"}}
