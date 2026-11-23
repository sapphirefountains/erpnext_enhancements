"""The Fountain Designer's design model, schema 2: dataclasses, JSON round-trip, validation
and the migration from the old child-table shape.

``docs/fountain-designer/PLAN.md`` section 3.3 is the spec. One JSON document (the Water
Feature Design's ``model_json``) holds the whole design: ``site``, the per-section ``setup``
policy (``auto`` | ``custom`` | a preset key), ``features``, ``basins``, the pumped
``network`` (nodes, pumps, segments, terminals), ``equipment``, ``treatment``, ``lighting``,
``electrical``, ``winterization``, ``acks`` and ``notes``.

Round-trip is **lossless**: every record keeps the keys it does not know in ``extra`` and
writes them back, so a page or an AI tool newer than this engine never has data dropped by
a load and save. ``from_dict(to_dict(m)) == m`` for any model this module produced, and
``to_dict(from_dict(d)) == d`` for any schema-2 dict whose known keys hold the documented
types.

:func:`validate` returns typed :class:`Problem` rows; it never raises on bad data.
:func:`migrate` reads the two schema-1 shapes (``water_feature_design._engine_inputs`` and
the ``save_water_design`` AI tool's payload) and returns a schema-2 dict.

This module is stdlib-only, like the rest of ``engine/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

SCHEMA = 2
MODES = ("quick", "full")
POLICIES = ("auto", "custom")
SETUP_SECTIONS = (
    "features", "basin", "piping", "pump", "control", "bypass", "surge",
    "treatment", "lighting", "electrical", "winterization",
)
NODE_KINDS = ("basin", "pump", "junction", "terminal")
TERMINAL_KINDS = ("open_discharge", "orifice", "manifold", "fixed_flow")
LINES = ("suction", "discharge")
CONTROLS = ("none", "vfd", "prv")

ERROR = "error"
WARNING = "warning"


# --------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------


class Record:
    """Shared ``from_dict`` / ``to_dict`` for the model's dataclasses.

    ``_nested`` maps a field to the record class of its value, ``_lists`` a list field to the
    class of its items, and ``_aliases`` a JSON key to the attribute it lives in (``from`` is a
    Python keyword). Unknown keys go to ``extra`` and come back out on ``to_dict``.
    """

    _nested: ClassVar[dict[str, type]] = {}
    _lists: ClassVar[dict[str, type]] = {}
    _aliases: ClassVar[dict[str, str]] = {}

    @classmethod
    def from_dict(cls, data: Any):
        if isinstance(data, cls):
            return data
        data = data if isinstance(data, dict) else {}
        names = {f.name for f in fields(cls)} - {"extra"}
        kwargs: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in data.items():
            attr = cls._aliases.get(key, key)
            if attr not in names:
                extra[key] = value
            elif attr in cls._nested:
                kwargs[attr] = cls._nested[attr].from_dict(value)
            elif attr in cls._lists:
                kwargs[attr] = [cls._lists[attr].from_dict(v) for v in (value or [])]
            else:
                kwargs[attr] = value
        if "extra" in {f.name for f in fields(cls)}:
            kwargs["extra"] = extra
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        reverse = {v: k for k, v in self._aliases.items()}
        for f in fields(self):
            if f.name == "extra":
                continue
            value = getattr(self, f.name)
            if isinstance(value, Record):
                value = value.to_dict()
            elif isinstance(value, list):
                value = [v.to_dict() if isinstance(v, Record) else v for v in value]
            out[reverse.get(f.name, f.name)] = value
        out.update(getattr(self, "extra", None) or {})
        return out


@dataclass(eq=True)
class Site(Record):
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    elevation_ft: float | None = None
    air_temp_f: float | None = None
    water_temp_f: float | None = None
    frost_depth_in: float | None = None
    hardness_ppm: float | None = None
    kwh_rate: float | None = None
    water_rate_per_ccf: float | None = None
    jurisdiction: str = "UT"
    venue_type: str = "Decorative Fountain"
    regulated: bool = False
    # ASCE 7 Seismic Design Category: the USGS lookup for the point, else the state's default (phase J).
    # The lookup's SDS and SD1 ride in ``extra`` as ``sds`` and ``sd1``; ``extra.sources`` records where
    # each engine-filled site value came from (``derive.derive_site``).
    seismic_sdc: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Setup(Record):
    features: str = "custom"
    basin: str = "auto"
    piping: str = "auto"
    pump: str = "auto"
    control: str = "auto"
    bypass: str = "auto"
    surge: str = "auto"
    treatment: str = "auto"
    lighting: str = "auto"
    electrical: str = "auto"
    winterization: str = "auto"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Feature(Record):
    id: str = ""
    family: str = ""
    type: str = ""
    label: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    effect: dict[str, Any] = field(default_factory=dict)
    product: dict[str, Any] = field(default_factory=lambda: {"item": None, "generic": True})
    count: int = 1
    basin: str | None = None
    node: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Basin(Record):
    id: str = ""
    label: str = ""
    shape: str = "rectangular"
    length_in: float | None = None
    width_in: float | None = None
    depth_in: float | None = None
    diameter_in: float | None = None
    freeboard_in: float | None = None
    volume_gal_override: float | None = None
    elevation_ft: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Node(Record):
    id: str = ""
    kind: str = "junction"
    basin: str | None = None
    feature: str | None = None
    elevation_ft: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Pump(Record):
    id: str = ""
    item: str | None = None
    curve: Any = None
    speed_pct: float = 100.0
    control: str = "none"
    setpoint_psi: float | None = None
    generic_class: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Segment(Record):
    _aliases: ClassVar[dict[str, str]] = {"from": "from_"}

    id: str = ""
    from_: str = ""
    to: str = ""
    line: str = "discharge"
    material: str = "SCH40 PVC"
    nominal_size: str | None = None
    length_ft: float = 0.0
    fittings: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Terminal(Record):
    id: str = ""
    kind: str = "open_discharge"
    feature: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Network(Record):
    _lists: ClassVar[dict[str, type]] = {"nodes": Node, "pumps": Pump, "segments": Segment, "terminals": Terminal}

    nodes: list[Node] = field(default_factory=list)
    pumps: list[Pump] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    terminals: list[Terminal] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Equipment(Record):
    slot: str = ""
    item: str | None = None
    qty: float = 1
    auto: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Treatment(Record):
    sanitizer: str = "chlorine"
    uv: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Lighting(Record):
    fixtures: list[dict[str, Any]] = field(default_factory=list)
    control: str = "switched"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Electrical(Record):
    service_v: float | None = 240
    phase: int | None = 1
    panel_distance_ft: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Winterization(Record):
    """Items the designer pinned (``setup.winterization == "custom"``); the generated
    checklist lives in the derive results, not here."""

    items: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(eq=True)
class Model(Record):
    _nested: ClassVar[dict[str, type]] = {
        "site": Site, "setup": Setup, "network": Network, "treatment": Treatment,
        "lighting": Lighting, "electrical": Electrical, "winterization": Winterization,
    }
    _lists: ClassVar[dict[str, type]] = {"features": Feature, "basins": Basin, "equipment": Equipment}

    schema: int = SCHEMA
    mode: str = "quick"
    kit: str | None = None
    site: Site = field(default_factory=Site)
    setup: Setup = field(default_factory=Setup)
    features: list[Feature] = field(default_factory=list)
    basins: list[Basin] = field(default_factory=list)
    network: Network = field(default_factory=Network)
    equipment: list[Equipment] = field(default_factory=list)
    treatment: Treatment = field(default_factory=Treatment)
    lighting: Lighting = field(default_factory=Lighting)
    electrical: Electrical = field(default_factory=Electrical)
    winterization: Winterization = field(default_factory=Winterization)
    acks: list[Any] = field(default_factory=list)
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def from_dict(data: dict[str, Any] | str | Model | None) -> Model:
    """A :class:`Model` from a dict or a JSON string (``model_json`` as stored)."""
    if isinstance(data, str):
        data = json.loads(data or "{}")
    return Model.from_dict(data or {})


def to_dict(model: Model | dict[str, Any]) -> dict[str, Any]:
    return model.to_dict() if isinstance(model, Model) else dict(model)


def new_model(**overrides: Any) -> dict[str, Any]:
    """An empty schema-2 model dict with the defaults filled in. Its default state is tagged as the
    engine's (``default_state``) unless the caller named one."""
    out = Model.from_dict(overrides).to_dict()
    named = isinstance(overrides.get("site"), dict) and "jurisdiction" in overrides["site"]
    return out if named else default_state(out)


def default_state(model: dict[str, Any]) -> dict[str, Any]:
    """Tag a new model's state (``Site.jurisdiction``, "UT") as a default in ``site.sources``, the way
    ``derive.derive_site`` records what the engine filled, so the address's state replaces it on a save
    (v1.774.0). Untagged, it would count as the designer's pick and an address in another state would
    never move it. The page's ``model.blank_model`` and ``model_from_kit`` tag theirs the same way. A
    state that already has a source is left alone. Changes ``model`` in place and returns it."""
    site = model.get("site") if isinstance(model.get("site"), dict) else None
    code = str((site or {}).get("jurisdiction") or "").strip()
    if not code:
        return model
    sources = site.get("sources") if isinstance(site.get("sources"), dict) else {}
    if "jurisdiction" not in sources:
        site["sources"] = {**sources, "jurisdiction": {"src": "default", "v": code}}
    return model


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


@dataclass
class Problem:
    """One thing wrong with a model. ``path`` points at it (``features[0].basin``)."""

    code: str
    severity: str
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "path": self.path, "message": self.message}


def _custom_content(m: Model) -> dict[str, bool]:
    """Whether each setup section has anything in it, for the ``custom`` check."""
    slots = {e.slot for e in m.equipment}
    return {
        "features": bool(m.features),
        "basin": bool(m.basins),
        "piping": bool(m.network.segments),
        "pump": bool(m.network.pumps),
        "control": bool(m.network.pumps),
        "bypass": "bypass" in slots,
        "surge": "surge_arrestor" in slots,
        "treatment": bool(m.treatment.sanitizer) or m.treatment.uv or bool(m.treatment.extra),
        "lighting": bool(m.lighting.fixtures),
        "electrical": bool(m.electrical.service_v),
        "winterization": bool(m.winterization.items),
    }


def validate(model: Model | dict[str, Any] | str) -> list[Problem]:
    """Every structural problem in ``model``: unknown family, a feature whose basin or node
    does not exist, a segment with an unknown endpoint, a ``custom`` section with nothing in
    it, duplicate ids, unknown kinds and policies. Engineering findings are not here; they
    come from the families' ``checks``."""
    from .families import FAMILIES  # deferred: families import nothing from here, but keep the edge one-way

    try:
        m = model if isinstance(model, Model) else from_dict(model)
    except (TypeError, ValueError) as exc:
        return [Problem("unreadable", ERROR, "", f"The model could not be read: {exc}")]
    out: list[Problem] = []

    if m.schema != SCHEMA:
        out.append(Problem("schema", ERROR, "schema", f"Schema {m.schema!r}; this engine reads schema {SCHEMA}. Run migrate()."))
    if m.mode not in MODES:
        out.append(Problem("mode", WARNING, "mode", f"Unknown mode {m.mode!r}; expected quick or full."))

    for section in SETUP_SECTIONS:
        policy = getattr(m.setup, section)
        if not isinstance(policy, str) or not policy:
            out.append(Problem("setup_policy", ERROR, f"setup.{section}", f"Section {section} has no policy."))
    content = _custom_content(m)
    for section in SETUP_SECTIONS:
        if getattr(m.setup, section) == "custom" and not content[section]:
            out.append(Problem("empty_custom", ERROR, f"setup.{section}",
                               f"{section} is set to custom but has nothing in it; set it to auto or fill it in."))

    def dupes(ids: list[str], path: str) -> set[str]:
        seen, dup = set(), set()
        for i, x in enumerate(ids):
            if not x:
                out.append(Problem("missing_id", ERROR, f"{path}[{i}].id", f"{path}[{i}] has no id."))
            elif x in seen:
                dup.add(x)
            seen.add(x)
        for x in sorted(dup):
            out.append(Problem("duplicate_id", ERROR, path, f"Id {x!r} is used twice in {path}."))
        return seen

    basin_ids = dupes([b.id for b in m.basins], "basins")
    feature_ids = dupes([f.id for f in m.features], "features")
    node_ids = dupes([n.id for n in m.network.nodes], "network.nodes")
    pump_ids = dupes([p.id for p in m.network.pumps], "network.pumps")
    dupes([s.id for s in m.network.segments], "network.segments")
    dupes([t.id for t in m.network.terminals], "network.terminals")

    for i, f in enumerate(m.features):
        p = f"features[{i}]"
        if f.family not in FAMILIES:
            out.append(Problem("unknown_family", ERROR, f"{p}.family", f"Feature {f.id or i} has unknown family {f.family!r}."))
        if f.basin and f.basin not in basin_ids:
            out.append(Problem("unknown_basin", ERROR, f"{p}.basin", f"Feature {f.id or i} lands in basin {f.basin!r}, which does not exist."))
        if f.node and f.node not in node_ids:
            out.append(Problem("unknown_node", ERROR, f"{p}.node", f"Feature {f.id or i} is fed from node {f.node!r}, which does not exist."))
        try:
            if int(f.count) < 1:
                raise ValueError
        except (TypeError, ValueError):
            out.append(Problem("bad_count", ERROR, f"{p}.count", f"Feature {f.id or i} count must be a whole number of at least 1."))

    for i, n in enumerate(m.network.nodes):
        p = f"network.nodes[{i}]"
        if n.kind not in NODE_KINDS:
            out.append(Problem("unknown_node_kind", ERROR, f"{p}.kind", f"Node {n.id} has unknown kind {n.kind!r}."))
        if n.kind == "basin" and n.basin and n.basin not in basin_ids:
            out.append(Problem("unknown_basin", ERROR, f"{p}.basin", f"Node {n.id} draws from basin {n.basin!r}, which does not exist."))
        if n.kind == "terminal" and n.feature and n.feature not in feature_ids:
            out.append(Problem("unknown_feature", ERROR, f"{p}.feature", f"Node {n.id} feeds feature {n.feature!r}, which does not exist."))
        if n.kind == "pump" and n.id not in pump_ids:
            out.append(Problem("unknown_pump", WARNING, p, f"Pump node {n.id} has no pump record with the same id."))

    reached = {s.to for s in m.network.segments}
    if m.network.segments:
        for i, n in enumerate(m.network.nodes):
            if n.kind == "terminal" and n.id not in reached:
                out.append(Problem("unreachable_terminal", WARNING, f"network.nodes[{i}]",
                                   f"Terminal {n.id} has no pipe reaching it."))

    for i, s in enumerate(m.network.segments):
        p = f"network.segments[{i}]"
        for end, value in (("from", s.from_), ("to", s.to)):
            if value not in node_ids:
                out.append(Problem("unknown_endpoint", ERROR, f"{p}.{end}", f"Segment {s.id or i} {end} {value!r} is not a node."))
        if s.line not in LINES:
            out.append(Problem("unknown_line", WARNING, f"{p}.line", f"Segment {s.id or i} line {s.line!r}; expected suction or discharge."))

    for i, pmp in enumerate(m.network.pumps):
        if pmp.control not in CONTROLS:
            out.append(Problem("unknown_control", WARNING, f"network.pumps[{i}].control", f"Pump {pmp.id} control {pmp.control!r}."))

    for i, t in enumerate(m.network.terminals):
        p = f"network.terminals[{i}]"
        if t.kind not in TERMINAL_KINDS:
            out.append(Problem("unknown_terminal_kind", ERROR, f"{p}.kind", f"Terminal {t.id} has unknown kind {t.kind!r}."))
        if t.feature and t.feature not in feature_ids:
            out.append(Problem("unknown_feature", ERROR, f"{p}.feature", f"Terminal {t.id} serves feature {t.feature!r}, which does not exist."))
    return out


# --------------------------------------------------------------------------------------
# Migration from schema 1
# --------------------------------------------------------------------------------------


def _loads(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _f(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _fittings(rows: Any) -> list[dict[str, Any]]:
    """Schema-1 fittings (``{type, qty}`` or ``{fitting, qty}``) to schema 2 (``{key, qty}``)."""
    out = []
    for r in _loads(rows):
        if not isinstance(r, dict):
            continue
        key = r.get("key") or r.get("type") or r.get("fitting") or r.get("component")
        if key:
            row = {"key": key, "qty": r.get("qty", 1)}
            out.append(row)
    return out


def _legacy_feature(i: int, row: dict[str, Any], basin_id: str | None) -> dict[str, Any]:
    from .families import family_for_type

    ftype = row.get("feature_type") or "Weir"
    family = family_for_type(ftype)
    label = row.get("feature_label") or row.get("label") or f"{ftype} {i + 1}"
    inputs: dict[str, Any] = {}
    product: dict[str, Any] = {"item": None, "generic": True}
    count = 1
    if family == "weir":
        n = _f(row.get("contractions", row.get("end_contractions")))
        inputs = {"length_ft": _f(row.get("weir_length_ft")), "head_in": _f(row.get("head_in")) or None,
                  "contractions": 2 if n is None else int(n)}
        if "spill" in ftype.lower():
            inputs["style"] = "spillway"
    elif family == "cascade":
        tiers = [{"label": t.get("tier_label") or t.get("label") or f"tier {j + 1}",
                  "diameter_in": _f(t.get("diameter_in")), "rim_height_in": _f(t.get("rim_height_in")),
                  "gpm_per_ft": _f(t.get("spill_gpm_per_ft") or row.get("gpm_per_ft"))}
                 for j, t in enumerate(row.get("tiers") or [])]
        inputs = {"tiers": tiers}
    elif family == "curtain":
        inputs = {"style": "strand", "strand_count": int(_f(row.get("nozzle_count")) or 0) or None,
                  "gpm_per_strand": _f(row.get("gpm_each")) or None}
    else:
        count = max(int(_f(row.get("nozzle_count")) or 1), 1)
        inputs = {"supply_head_ft": _f(row.get("supply_head_ft"))}
        if family == "interactive":
            inputs = {"play_features": [{"label": label, "gpm": _f(row.get("gpm_each")), "count": count}]}
            count = 1
        profile = {k: row[k] for k in ("cd", "orifice_diameter_in", "orifice_area_in2", "rated_gpm", "rated_head_ft")
                   if row.get(k) not in (None, "", 0)}
        if row.get("nozzle_profile"):
            product = {"item": None, "generic": False, "nozzle_profile": row["nozzle_profile"], "profile": profile}
        elif profile:
            product = {"item": None, "generic": True, "profile": profile}
        if _f(row.get("gpm_each")) and family != "interactive" and ftype != "Orifice Nozzle":
            # The old engine sized arrays as count x gpm_each (nozzle_array_flow) and orifice
            # nozzles from the profile alone; keep each basis rather than overwrite the profile.
            inputs["gpm_each"] = _f(row.get("gpm_each"))
    legacy = {k: v for k, v in row.items() if k not in ("tiers",)}
    return {
        "id": f"f{i + 1}", "family": family, "type": ftype, "label": label,
        "inputs": {k: v for k, v in inputs.items() if v is not None},
        "effect": {}, "product": product, "count": count, "basin": basin_id, "node": f"t{i + 1}",
        "legacy": legacy,
    }


def is_legacy(data: dict[str, Any]) -> bool:
    """True for a schema-1 dict (either shape), false for schema 2."""
    if not isinstance(data, dict):
        return False
    if data.get("schema") == SCHEMA:
        return False
    return any(k in data for k in ("pipe_segments", "global_inputs", "static_lift_ft", "turnovers_per_hr")) or (
        any(isinstance(f, dict) and "feature_type" in f for f in data.get("features") or [])
    ) or ("basins" in data and "schema" not in data and "network" not in data)


def migrate(old: dict[str, Any] | str) -> dict[str, Any]:
    """Schema-1 design inputs to a schema-2 model dict.

    Accepts ``water_feature_design._engine_inputs(doc)`` and the ``save_water_design`` AI
    payload (``global_inputs`` + row lists with ``segment_label`` / ``pipe_length_ft`` /
    ``fittings_json``). A dict that is already schema 2 comes back normalized.

    Sizing: a filled-in old row sizes as the old engine sized it. A weir row with no head
    takes its head from the default look now (the old engine sized it at 0 GPM).

    Schema 1 has no topology, so the network is built the only way the old engine read it:
    every suction row in series from the first basin to one pump, every discharge row in
    series from the pump to the first feature, and every other feature on a branch of its own
    off the pump's discharge. ``static_lift_ft`` becomes the first terminal's elevation. Every
    old field that schema 2 has no place for is kept under the model's top-level ``legacy``
    key (and each feature's old row under its own ``legacy`` key), so nothing is lost.
    """
    if isinstance(old, str):
        old = json.loads(old or "{}")
    old = dict(old or {})
    if not is_legacy(old):
        return Model.from_dict(old).to_dict()

    g = dict(old.get("global_inputs") or {})
    get = lambda k, default=None: old.get(k, g.get(k, default))  # noqa: E731

    basins = []
    for i, b in enumerate(old.get("basins") or []):
        shape = str(b.get("shape") or "Rectangular").lower()
        shape = "round" if shape.startswith("cyl") else ("other" if shape.startswith("other") else "rectangular")
        row = {"id": f"b{i + 1}", "label": b.get("basin_label") or b.get("label") or f"Basin {i + 1}", "shape": shape,
               "length_in": _f(b.get("length_in")), "width_in": _f(b.get("width_in")),
               "depth_in": _f(b.get("height_in")), "diameter_in": _f(b.get("diameter_in")),
               "freeboard_in": None, "volume_gal_override": _f(b.get("volume_gal_override")) or None,
               "elevation_ft": 0.0}
        if _f(b.get("surface_area_sf")):
            row["surface_area_sqft"] = _f(b.get("surface_area_sf"))
        basins.append(row)
    first_basin = basins[0]["id"] if basins else None

    features = [_legacy_feature(i, f, first_basin) for i, f in enumerate(old.get("features") or [])]

    lift = _f(get("static_lift_ft")) or 0.0
    nodes: list[dict[str, Any]] = []
    if first_basin:
        nodes.append({"id": "src", "kind": "basin", "basin": first_basin, "elevation_ft": 0.0})
    pumps = [{"id": "p1", "item": None, "curve": None, "speed_pct": 100, "control": "none",
              "setpoint_psi": None, "generic_class": None}]
    nodes.append({"id": "p1", "kind": "pump"})
    for i, f in enumerate(features):
        nodes.append({"id": f"t{i + 1}", "kind": "terminal", "feature": f["id"], "elevation_ft": lift if i == 0 else 0.0})
    default_material = get("pipe_material") or "SCH40 PVC"

    seg_rows = old.get("pipe_segments") or []
    suction = [s for s in seg_rows if str(s.get("line_type") or "Discharge").lower() == "suction"]
    discharge = [s for s in seg_rows if str(s.get("line_type") or "Discharge").lower() != "suction"]
    segments: list[dict[str, Any]] = []
    j_count = 0

    def chain(rows: list[dict[str, Any]], start: str, end: str, line: str) -> None:
        nonlocal j_count
        here = start
        for k, s in enumerate(rows):
            if k == len(rows) - 1:
                to = end
            else:
                j_count += 1
                to = f"j{j_count}"
                nodes.append({"id": to, "kind": "junction", "elevation_ft": None})
            seg = {
                "id": f"s{len(segments) + 1}", "from": here, "to": to, "line": line,
                "material": s.get("material") or default_material, "nominal_size": s.get("nominal_size") or None,
                "length_ft": _f(s.get("length_ft", s.get("pipe_length_ft"))) or 0.0,
                "fittings": _fittings(s.get("fittings", s.get("fittings_json"))),
                "components": _fittings(s.get("components", s.get("components_json"))),
                "label": s.get("label") or s.get("segment_label") or "",
            }
            if _f(s.get("flow_gpm")):
                seg["flow_gpm"] = _f(s.get("flow_gpm"))
            if s.get("system"):
                seg["system"] = s["system"]
            segments.append(seg)
            here = to

    unplaced = []
    if first_basin and suction:
        chain(suction, "src", "p1", "suction")
    else:
        unplaced += suction
    if features and discharge:
        chain(discharge, "p1", "t1", "discharge")
    else:
        unplaced += discharge
    # Every other feature (and the first, when there were no discharge rows) gets a branch
    # of its own off the pump, unsized, as kits._network builds them.
    for i, f in enumerate(features):
        if i == 0 and discharge:
            continue
        segments.append({"id": f"s{len(segments) + 1}", "from": "p1", "to": f["node"], "line": "discharge",
                         "material": default_material, "nominal_size": None, "length_ft": 0.0,
                         "fittings": [], "components": []})

    terminals = []
    from .families import FAMILIES

    for f in features:
        module = FAMILIES.get(f["family"])
        try:
            term = module.terminal(f) if module else {"kind": "open_discharge", "params": {}}
        except Exception:  # a half-filled legacy row must still migrate
            term = {"kind": "open_discharge", "params": {}}
        terminals.append({"id": f["node"], "kind": term["kind"], "feature": f["id"], "params": term["params"]})

    venue = get("venue_type") or "Decorative Fountain"
    site = {"jurisdiction": "UT", "venue_type": venue, "regulated": False}
    try:
        from .aquatic import is_regulated

        site["regulated"] = bool(is_regulated(venue))
    except Exception:
        pass
    if get("governing_code"):
        site["governing_code"] = get("governing_code")

    equipment = []
    for p in old.get("pumps") or []:
        if p.get("pump_item"):
            equipment.append({"slot": "pump", "item": p["pump_item"], "qty": 1, "auto": False})
    consumed = {"basins", "features", "pipe_segments", "global_inputs", "static_lift_ft", "venue_type",
                "pipe_material", "governing_code"}
    legacy = {k: v for k, v in old.items() if k not in consumed}
    legacy.update({k: v for k, v in g.items() if k not in consumed})
    if unplaced:
        # No basin to draw from (or no feature to feed): nowhere to put them in the network.
        legacy["unplaced_segments"] = unplaced

    model = {
        "schema": SCHEMA, "mode": "full", "kit": None, "site": site,
        "setup": {**Setup().to_dict(), "basin": "custom" if basins else "auto",
                  "piping": "custom" if segments else "auto"},
        "features": features, "basins": basins,
        "network": {"nodes": nodes, "pumps": pumps, "segments": segments, "terminals": terminals},
        "equipment": equipment, "treatment": {"sanitizer": "chlorine", "uv": False},
        "lighting": {"fixtures": [], "control": "switched"},
        "electrical": {"service_v": 240, "phase": 1, "panel_distance_ft": None},
        "winterization": {"items": []}, "acks": [], "notes": "", "legacy": legacy, "migrated_from": 1,
    }
    # A schema-1 design never had a state: the "UT" above is the house default, not a choice.
    return default_state(Model.from_dict(model).to_dict())
