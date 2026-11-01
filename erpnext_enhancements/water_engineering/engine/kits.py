"""Kits: whole-design starting points, the "start from" catalog of quick mode.

A kit is a bundle of feature presets, the basins they land in, and a ``setup`` policy per
section (the rain-curtain app's ``KITS`` pattern; PLAN.md section 2.1). Kits are data, not
code: a preset value here is a starting point the designer edits, never an engineering rule.

* :func:`apply_kit` lays a kit onto a model: its features and basins replace the model's,
  every section's setup policy is reset to the kit's, a pump-and-pipes network skeleton is built
  (every pipe size left ``None`` so the piping step sizes it), and the site, electrical,
  acknowledgements and notes are kept.
* :func:`match_kit` says which kit a model still is, or ``"custom"`` once any value a kit
  sets has been changed (the app's ``matchPreset``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .families.curtain import DRC_APP as DRC_LINK
from .model import SETUP_SECTIONS, Model, Setup

CUSTOM = "custom"


@dataclass(frozen=True)
class Kit:
    key: str
    label: str
    family: str
    description: str = ""
    features: tuple[dict[str, Any], ...] = ()
    basins: tuple[dict[str, Any], ...] = ()
    setup: dict[str, str] = field(default_factory=dict)
    site: dict[str, Any] = field(default_factory=dict)
    lighting: dict[str, Any] = field(default_factory=dict)
    pump: dict[str, Any] = field(default_factory=dict)
    link: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "family": self.family, "description": self.description,
            "features": [copy.deepcopy(f) for f in self.features], "basins": [copy.deepcopy(b) for b in self.basins],
            "setup": dict(self.setup), "site": dict(self.site), "lighting": dict(self.lighting),
            "pump": dict(self.pump), "link": self.link,
        }


def _f(family: str, type_: str, label: str, inputs: dict[str, Any], count: int = 1,
       effect: dict[str, Any] | None = None, basin: int = 0) -> dict[str, Any]:
    """A feature preset. ``basin`` is the index of the kit basin it lands in."""
    return {"family": family, "type": type_, "label": label, "inputs": inputs, "effect": effect or {},
            "count": count, "basin": basin}


def _b(label: str, shape: str = "rectangular", **dims: Any) -> dict[str, Any]:
    return {"label": label, "shape": shape, **dims}


KITS: tuple[Kit, ...] = (
    Kit(
        "lobby-sheet-wall-8ft", "Hotel lobby sheet wall 8 ft", "weir",
        "An indoor 8 ft sheet running down a 10 ft wall into a trough, submersible pump, wide lights.",
        features=(_f("weir", "Waterwall", "Lobby wall",
                     {"style": "waterwall", "length_ft": 8, "fall_ft": 10, "lip": "square", "tolerance_in": 0.0625,
                      "indoor": True}, effect={"look": "average"}),),
        basins=(_b("Trough", length_in=120, width_in=24, depth_in=14),),
        pump={"generic_class": "submersible", "location": "submersible"},
        lighting={"control": "switched"},
    ),
    Kit(
        "weir-spillway-outdoor-4ft", "Outdoor spillway 4 ft", "weir",
        "A 4 ft spillway falling 2 ft into a catch basin.",
        features=(_f("weir", "Spillway", "Spillway", {"style": "spillway", "length_ft": 4, "fall_ft": 2, "lip": "square"},
                     effect={"look": "average"}),),
        basins=(_b("Catch basin", length_in=72, width_in=36, depth_in=18),),
        pump={"generic_class": "end-suction", "location": "vault"},
    ),
    Kit(
        "jet-ring-6", "Jet ring, 6 nozzles at 6 ft", "jets",
        "Six smooth-bore jets on a ring in a round basin; pick the nozzle to replace the estimate.",
        features=(_f("jets", "Jet Ring", "Jet ring", {"height_ft": 6, "nozzle_type": "smooth", "ring_diameter_ft": 6,
                                                    "orifice_diameter_in": 0.375},
                     count=6),),
        basins=(_b("Basin", "round", diameter_in=240, depth_in=18),),
        pump={"generic_class": "end-suction", "location": "vault"},
    ),
    Kit(
        "foam-jet-single", "Foam jet 4 ft", "foam",
        "One level-sensitive foam jet with a check valve.",
        features=(_f("foam", "Foam Jet", "Foam jet", {"height_ft": 4, "depth_over_nozzle_in": 3, "check_valve": True,
                                                       "orifice_diameter_in": 1.0}),),
        basins=(_b("Basin", "round", diameter_in=144, depth_in=18),),
        pump={"generic_class": "end-suction", "location": "vault"},
    ),
    Kit(
        "laminar-pair", "Laminar jet pair", "laminar",
        "Two laminar arcs 3 ft high crossing a pool.",
        features=(_f("laminar", "Laminar Jet", "Laminar arcs", {"arc_height_ft": 3, "filtered": True}, count=2),),
        basins=(_b("Pool", length_in=144, width_in=96, depth_in=18),),
        pump={"generic_class": "end-suction", "location": "vault"},
    ),
    Kit(
        "plaza-interactive-12-jets", "Plaza interactive 12 jets", "interactive",
        "Twelve ground jets in three sequence steps of four, with a collector tank. Utah IWF rules apply.",
        features=(_f("interactive", "Interactive Plaza", "Plaza jets", {
            "play_features": [
                {"label": "row A", "gpm": 10, "count": 4},
                {"label": "row B", "gpm": 10, "count": 4},
                {"label": "row C", "gpm": 10, "count": 4},
            ],
            "sequence": [{"open": "row A"}, {"open": "row B"}, {"open": "row C"}, {"open": "row A, row C"}],
        }),),
        basins=(_b("Collector tank", length_in=96, width_in=60, depth_in=48),),
        site={"venue_type": "Interactive Water Feature", "regulated": True},
        pump={"generic_class": "end-suction", "location": "vault"},
    ),
    Kit(
        "cascade-three-tier", "Three-tier cascade", "cascade",
        "Round tiers of 24, 36 and 48 in spilling all the way round.",
        features=(_f("cascade", "Tiered Fountain", "Tiers", {"tiers": [
            {"label": "top", "diameter_in": 24}, {"label": "middle", "diameter_in": 36},
            {"label": "bottom", "diameter_in": 48},
        ]}, effect={"look": "average"}),),
        basins=(_b("Pool", "round", diameter_in=96, depth_in=18),),
        pump={"generic_class": "end-suction", "location": "vault"},
    ),
    Kit(
        "pondless-stream-10ft", "Pondless stream 10 ft", "pondless",
        "A 12 in spillway feeding a 10 ft stream over a water-matrix reservoir.",
        features=(_f("pondless", "Pondless Waterfall", "Stream", {
            "kind": "stream", "spill_width_in": 12, "stream_length_ft": 10, "stream_width_ft": 2,
            "stream_depth_in": 2, "fill": "matrix"}),),
        pump={"generic_class": "submersible", "location": "submersible"},
    ),
    Kit(
        "curtain-passive-6ft", "Passive rain curtain 6 ft", "curtain",
        "A 6 ft strand curtain.",
        features=(_f("curtain", "Rain Curtain", "Curtain", {"style": "strand", "width_ft": 6, "spacing_in": 1.5}),),
        basins=(_b("Catch trough", length_in=78, width_in=18, depth_in=12),),
        pump={"generic_class": "submersible", "location": "submersible"},
    ),
    Kit(
        "event-rental-curtain", "Event rental curtain", "curtain",
        "Sapphire's digital rain curtain for events. Design the curtain itself in the Digital Rain Curtain app.",
        features=(_f("curtain", "Digital Rain Curtain", "Rental curtain", {"style": "digital", "width_ft": 8}),),
        site={"venue_type": "Event Rental"},
        link=DRC_LINK,
    ),
    Kit(
        "mist-patio-20", "Patio mist, 20 nozzles", "mist",
        "Twenty 0.012 in nozzles on a potable high-pressure line.",
        features=(_f("mist", "Mist", "Mist ring", {"nozzle_count": 20, "orifice_in": "0.012", "supply": "potable"}),),
    ),
    Kit(
        "pond-aerator-1-acre", "Pond aerator 1 acre", "aerator",
        "One acre of pond aerated at 1 HP per acre.",
        features=(_f("aerator", "Floating Aerator", "Aerator", {"surface_acres": 1, "purpose": "aeration", "unit_hp": 1}),),
    ),
)

KITS_BY_KEY: dict[str, Kit] = {k.key: k for k in KITS}


def get_kit(kit: str | Kit | dict[str, Any] | None) -> Kit | None:
    if isinstance(kit, Kit):
        return kit
    if isinstance(kit, dict):
        return KITS_BY_KEY.get(kit.get("key"))
    return KITS_BY_KEY.get(kit or "")


def catalog() -> list[dict[str, Any]]:
    return [k.to_dict() for k in KITS]


def kit_setup(kit: Kit) -> dict[str, str]:
    """The kit's policy for every section: its own entries over the quick-mode default
    (features ``custom``, everything else ``auto``). Applying a kit resets every section's
    policy to this, which is what makes a kit a whole-design starting point."""
    return {**{s: v for s, v in Setup().to_dict().items() if s in SETUP_SECTIONS}, "features": "custom", **kit.setup}


def _pumped(feature: dict[str, Any]) -> bool:
    """False for features that are not a terminal on the pumped network: a floating aerator
    (self-contained), mist (its own potable loop) and a digital curtain (designed in its app)."""
    if feature["family"] in ("aerator", "mist"):
        return False
    return not (feature["family"] == "curtain" and (feature.get("inputs") or {}).get("style") == "digital")


def _network(features: list[dict[str, Any]], basins: list[dict[str, Any]], pump: dict[str, Any]) -> dict[str, Any]:
    """Pump-and-pipes skeleton: basin -> pump (suction) -> each feature (discharge)."""
    from .families import FAMILIES

    nodes: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []
    pumped = [f for f in features if _pumped(f)]
    if not pumped:
        return {"nodes": [], "pumps": [], "segments": [], "terminals": []}
    pump_row = {"id": "p1", "item": None, "curve": None, "speed_pct": 100, "control": "none", "setpoint_psi": None,
                "generic_class": pump.get("generic_class")}
    if pump.get("location"):
        pump_row["location"] = pump["location"]
    if basins:
        nodes.append({"id": "src", "kind": "basin", "basin": basins[0]["id"], "elevation_ft": 0.0})
    nodes.append({"id": "p1", "kind": "pump"})
    if basins:
        segments.append({"id": "s1", "from": "src", "to": "p1", "line": "suction", "material": "SCH40 PVC",
                         "nominal_size": None, "length_ft": 0.0, "fittings": [], "components": []})
    for f in pumped:
        term = FAMILIES[f["family"]].terminal(f)
        lift = term["params"].get("lift_ft") or 0.0
        nodes.append({"id": f["node"], "kind": "terminal", "feature": f["id"], "elevation_ft": lift})
        segments.append({"id": f"s{len(segments) + 1}", "from": "p1", "to": f["node"], "line": "discharge",
                         "material": "SCH40 PVC", "nominal_size": None, "length_ft": 0.0, "fittings": [],
                         "components": []})
        terminals.append({"id": f["node"], "kind": term["kind"], "feature": f["id"], "params": term["params"]})
    return {"nodes": nodes, "pumps": [pump_row], "segments": segments, "terminals": terminals}


def apply_kit(kit: str | Kit, model: Model | dict[str, Any] | None = None) -> dict[str, Any]:
    """``model`` (a new one when omitted) with ``kit`` laid onto it, as a schema-2 dict."""
    k = get_kit(kit)
    if k is None:
        raise ValueError(f"Unknown kit {kit!r}")
    base = (model.to_dict() if isinstance(model, Model) else Model.from_dict(model or {}).to_dict())
    out = copy.deepcopy(base)
    basins = []
    for i, b in enumerate(k.basins):
        row = {"id": f"b{i + 1}", "freeboard_in": None, "volume_gal_override": None, "elevation_ft": 0.0}
        row.update(copy.deepcopy(b))
        basins.append(row)
    features = []
    for i, f in enumerate(k.features):
        row = copy.deepcopy(f)
        bi = row.pop("basin", 0)
        row.update({"id": f"f{i + 1}", "node": f"t{i + 1}" if _pumped(row) else None,
                    "product": {"item": None, "generic": True},
                    "basin": basins[bi]["id"] if basins and bi < len(basins) else None})
        features.append(row)
    out["kit"] = k.key
    out["features"] = features
    out["basins"] = basins
    out["setup"] = {**(base.get("setup") or {}), **kit_setup(k)}
    out["site"] = {**(base.get("site") or {}), **copy.deepcopy(k.site)}
    out["network"] = _network(features, basins, k.pump)
    if k.lighting:
        out["lighting"] = {**(base.get("lighting") or {}), **copy.deepcopy(k.lighting)}
    if k.link:
        out["link"] = dict(k.link)
    return Model.from_dict(out).to_dict()


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    if isinstance(a, dict) and isinstance(b, dict):
        return all(k in b and _same(v, b[k]) for k, v in a.items())
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=False))
    return a == b


def _matches(k: Kit, m: dict[str, Any]) -> bool:
    feats = m.get("features") or []
    if len(feats) != len(k.features):
        return False
    for kf, mf in zip(k.features, feats, strict=False):
        if kf["family"] != mf.get("family") or kf["type"] != mf.get("type"):
            return False
        if int(kf.get("count") or 1) != int(mf.get("count") or 1):
            return False
        if not _same(kf["inputs"], mf.get("inputs") or {}) or not _same(kf.get("effect") or {}, mf.get("effect") or {}):
            return False
    bas = m.get("basins") or []
    if len(bas) != len(k.basins):
        return False
    for kb, mb in zip(k.basins, bas, strict=False):
        if not _same(kb, mb):
            return False
    setup = m.get("setup") or {}
    want = kit_setup(k)
    return all(setup.get(s) == want[s] for s in SETUP_SECTIONS)


def match_kit(model: Model | dict[str, Any]) -> str:
    """The kit ``model`` still is, or ``"custom"``. The model's own ``kit`` is tried first."""
    m = model.to_dict() if isinstance(model, Model) else Model.from_dict(model or {}).to_dict()
    first = get_kit(m.get("kit"))
    for k in ([first] if first else []) + [k for k in KITS if k is not first]:
        if _matches(k, m):
            return k.key
    return CUSTOM
