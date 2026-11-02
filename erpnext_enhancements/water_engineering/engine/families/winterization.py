"""Winterization and spring start-up: a per-site checklist generated from the design.

Industry report section 1.12. A **system module** (see :mod:`.lighting`): ``inputs()``,
``calc(model, derived=None, rules=None)``, ``checks(model, derived=None, rules=None)``.

It reads the schema-2 model:

* ``site.frost_depth_in`` and ``site.winterize``, which ``derive.derive_site`` fills from the state's
  row of the jurisdiction table (phase J). There is no Salt Lake City fallback: a blank frost depth
  stays unknown, and a state that does not freeze gets an INFO that winterization does not apply;
* each ``network.segments[*]``: ``drain`` (a drain point), ``slopes_to_drain`` and
  ``buried_depth_in`` (optional keys the route templates set);
* each ``network.pumps[*].location`` (``vault`` | ``room`` | ``submersible``) and ``heated``;
* the features' families (splash pads, level-sensitive jets) and ``lighting.fixtures``.

There is no public heat-trace rule, so none is generated.
"""

from __future__ import annotations

from typing import Any

from .. import jurisdiction as juris
from .base import INFO, WARNING, Check, FamilyResult, feature_dict, num

FAMILY = "winterization"
LABEL = "Winterization"

CIT_FROST = "IBC 2021 §1809.5 (footings and buried lines below the frost line); the frost depth is the site's, else its state's design city"
CIT_WHEN = "https://cdn.shopify.com/s/files/1/0566/4492/7571/files/Commercial_Fountain_Winterization_Instructions.pdf (below 32 F; disconnect the autofill)"
CIT_BLOWOUT = (
    "https://rainbird.com/media/1332 (blow out at <= 50 psi, <= 2 min per circuit, never through a pump or backflow "
    "preventer); a pool maker allows < 5 psi blowers only: follow the equipment maker's limit"
)
CIT_SPLASH = "https://www.vortex-intl.com/blog/winterize-water-play-system/ (pull upward-facing nozzles; fit winter caps)"



def _frost(site: dict[str, Any]) -> float | None:
    v = num(site.get("frost_depth_in"), -1.0)
    return v if v >= 0 else None


def inputs() -> list[dict[str, Any]]:
    return []


def _item(key: str, title: str, detail: str = "", citation: str = "", applies_to: str = "") -> dict[str, Any]:
    return {"key": key, "title": title, "detail": detail, "citation": citation, "applies_to": applies_to}


def calc(model: dict[str, Any], derived: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    site = model.get("site") or {}
    frost = _frost(site)
    needed = juris.winterize(site)
    net = model.get("network") or {}
    features = [feature_dict(f) for f in model.get("features") or []]
    families = {f.get("family") for f in features}
    winter: list[dict[str, Any]] = [
        _item("autofill", "Shut off and disconnect the autofill", citation=CIT_WHEN),
        _item("lower_level", "Lower the water below the returns and skimmers, or drain the basin"),
    ]
    for p in net.get("pumps") or []:
        loc = str(p.get("location") or "").lower()
        if loc == "submersible":
            winter.append(_item(f"pump_{p.get('id')}", f"Pull submersible pump {p.get('id')} and store it indoors",
                                applies_to=p.get("id") or ""))
        else:
            winter.append(_item(f"pump_{p.get('id')}", f"Drain pump {p.get('id')}: open the drain plugs, store them in the basket",
                                applies_to=p.get("id") or ""))
    winter.append(_item("equipment", "Drain the filter, heater and strainers; open every equipment drain"))
    for s in net.get("segments") or []:
        sid = s.get("id") or ""
        if s.get("drain") or s.get("slopes_to_drain"):
            winter.append(_item(f"seg_{sid}", f"Drain line {sid} at its drain point", applies_to=sid))
        elif frost is not None and num(s.get("buried_depth_in")) >= frost:
            winter.append(_item(f"seg_{sid}", f"Line {sid} is below the {frost:g} in frost depth; cap it",
                                citation=CIT_FROST, applies_to=sid))
        else:
            winter.append(_item(f"seg_{sid}", f"Blow out line {sid}", citation=CIT_BLOWOUT, applies_to=sid))
    for f in features:
        fam = f.get("family")
        product = f.get("product") or {}
        level = (product.get("profile") or {}).get("level_sensitive") or (f.get("inputs") or {}).get("level_sensitive")
        if fam == "interactive":
            winter.append(_item(f"nozzles_{f.get('id')}", "Pull the upward-facing nozzles and fit winter caps",
                                citation=CIT_SPLASH, applies_to=f.get("id") or ""))
        elif fam in ("jets", "foam", "laminar") and (level or fam != "jets"):
            winter.append(_item(f"nozzles_{f.get('id')}", f"Pull and store the {fam} nozzles on {f.get('label') or f.get('id')}",
                                applies_to=f.get("id") or ""))
    if (model.get("lighting") or {}).get("fixtures") or "jets" in families or "weir" in families:
        winter.append(_item("lights", "Pull the underwater lights or confirm they are rated to stay in frozen water"))
    winter.append(_item("controls", "Turn off and lock out the feature circuits; leave heat on in a heated room"))

    spring = [
        _item("inspect", "Inspect the basin and lines for freeze damage"),
        _item("refit", "Refit plugs, nozzles, lights and the submersible pumps"),
        _item("fill", "Fill, reconnect the autofill and prime the pumps"),
        _item("start", "Start the pumps, check for leaks and set the flow"),
        _item("chemistry", "Balance and sanitize the water before people use it"),
    ]
    return FamilyResult(
        calc="winterization_checklist",
        value=len(winter),
        unit="items",
        formula="checklist from routes, pumps, features and lights; frost depth from the site",
        steps=[(f"frost depth {frost:g} in" if frost is not None else "no frost depth on file")
               + ("" if needed else f"; {juris.state_name(site)} does not freeze: no winterization")],
        citations=[CIT_FROST, CIT_WHEN, CIT_BLOWOUT],
        data={"frost_depth_in": frost, "needed": needed, "winterize": winter, "spring_startup": spring},
    )


def checks(model: dict[str, Any], derived: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> list[Check]:
    site = model.get("site") or {}
    frost = _frost(site)
    net = model.get("network") or {}
    if not juris.winterize(site):
        return [Check(
            "WINTER_NOT_NEEDED", INFO, "Winterization does not apply",
            detail=f"{juris.state_name(site)} is not a freezing climate (the site's design city), so the design carries "
                   "no winterization checklist. Drain only for service.",
            remedy="Tick Winterize on the Site step if this site does freeze.", section="winterization",
            citation=str((juris.climate(juris.entry_for(site)).get("cites") or {}).get("winterize") or ""),
        )]
    out: list[Check] = []
    if frost is None:
        out.append(Check(
            "WINTER_FROST_UNKNOWN", INFO, "No frost depth for the site",
            detail="The lines' burial cannot be judged against the frost line.",
            remedy="Type the frost depth on the Site step (the building department publishes it).", section="winterization",
        ))
        frost = 0.0
    exposed = [
        s.get("id") or "" for s in net.get("segments") or []
        if not (s.get("drain") or s.get("slopes_to_drain")) and s.get("buried_depth_in") not in (None, "")
        and num(s.get("buried_depth_in")) < frost
    ]
    if exposed:
        out.append(Check(
            "WINTER_LINE_ABOVE_FROST", WARNING, "Lines above the frost depth with no drain",
            detail=f"{', '.join(exposed)}: buried shallower than {frost:g} in and not drained.",
            remedy="Add a drain point, slope them to drain, or bury them below the frost depth.",
            section="winterization", citation=CIT_FROST,
        ))
    undecided = [s.get("id") or "" for s in net.get("segments") or []
                 if not (s.get("drain") or s.get("slopes_to_drain")) and s.get("buried_depth_in") in (None, "")]
    if undecided:
        out.append(Check(
            "WINTER_DRAIN_UNKNOWN", INFO, "Say how these lines drain for winter",
            detail=", ".join(undecided) + ": no drain point, slope or burial depth recorded.",
            remedy="Mark each line drained, sloped to drain, or buried below frost.", section="winterization",
        ))
    for p in net.get("pumps") or []:
        if str(p.get("location") or "").lower() == "room" and not p.get("heated"):
            out.append(Check(
                "WINTER_ROOM_UNHEATED", INFO, f"Equipment room for pump {p.get('id')} is not marked heated",
                remedy="Heat the room or drain everything in it for winter.", section="winterization",
                citation=CIT_WHEN,
            ))
    return out
