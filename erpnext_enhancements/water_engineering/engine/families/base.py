"""The shared vocabulary every feature-family module speaks.

A family module exposes four functions:

* ``inputs() -> list[dict]``: the input schema the designer page renders, one
  :func:`spec` dict per input (name, label, kind, unit, default, required, help, options).
* ``flow(feature, site=None, rules=None) -> FamilyResult``: the feature's design flow. It is
  a :class:`~..envelope.CalcResult` (formula, steps, citations, warnings, options) with one
  extra field, ``data``, holding the structured numbers later steps read.
* ``checks(feature, derived=None, site=None, rules=None) -> list[Check]``: typed findings.
  ``derived`` is what the orchestrator already knows: ``derived["flow"]`` is the ``data`` of
  :func:`flow` (computed here when absent) and ``derived["basin"]`` the schema-2 basin the
  feature lands in. Each family's docstring lists any other key it reads.
* ``terminal(feature, derived=None) -> dict``: ``{"kind", "params"}`` for the hydraulics
  terminal layer: ``open_discharge`` (``lift_ft``), ``orifice`` (``cd`` + ``area_in2`` or a
  rated ``flow_gpm`` at ``head_ft``), ``manifold`` (``takeoffs``), or ``fixed_flow``
  (``flow_gpm``).

``feature`` is a schema-2 feature dict (or a :class:`~..model.Feature`):
``{"id", "family", "type", "label", "inputs", "effect", "product", "count", "basin", "node"}``.
:func:`values` merges ``effect`` over ``inputs`` over the family's input defaults, so a family
reads one flat dict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..envelope import CalcResult

# Check severities. The first three match ``issues.py`` so phase E maps them one to one;
# ``pass`` is for rule lists shown as pass/fail (the Utah interactive checklist).
BLOCKER = "blocker"
WARNING = "warning"
INFO = "info"
PASS = "pass"
SEVERITIES = (BLOCKER, WARNING, INFO, PASS)

# The page section a check jumps to. These are the model's own section keys (schema 2).
SECTION_KEYS = (
    "site", "features", "basin", "piping", "pump", "control", "bypass", "surge",
    "treatment", "lighting", "electrical", "winterization",
)

INPUT_KINDS = ("number", "integer", "select", "bool", "text", "table")

GPM_PER_GPH = 1.0 / 60.0
GAL_PER_CUBIC_FOOT = 7.480519
FT_PER_M = 1.0 / 0.3048
G_FT_S2 = 9.80665 / 0.3048  # standard gravity, 32.174 ft/s^2


@dataclass
class FamilyResult(CalcResult):
    """A ``CalcResult`` plus ``data``: the structured numbers downstream steps read."""

    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Check:
    """One typed finding from a family's rules."""

    code: str
    severity: str
    title: str
    section: str = "features"
    detail: str = ""
    remedy: str = ""
    citation: str = ""
    feature: str = ""
    value: Any = None
    limit: Any = None
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def spec(
    name: str,
    label: str,
    kind: str = "number",
    unit: str = "",
    default: Any = None,
    required: bool = False,
    help: str = "",
    options: list[Any] | None = None,
    columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One input row for the page. ``columns`` describes a ``table`` input's rows."""
    row = {
        "name": name,
        "label": label,
        "kind": kind,
        "unit": unit,
        "default": default,
        "required": required,
        "help": help,
    }
    if options is not None:
        row["options"] = list(options)
    if columns is not None:
        row["columns"] = columns
    return row


def feature_dict(feature: Any) -> dict[str, Any]:
    """A schema-2 feature as a plain dict, whether given a dict or a ``Feature``."""
    if feature is None:
        return {}
    if isinstance(feature, dict):
        return feature
    to_dict = getattr(feature, "to_dict", None)
    return to_dict() if callable(to_dict) else dict(vars(feature))


def values(feature: Any, schema: list[dict[str, Any]]) -> dict[str, Any]:
    """The family's flat input dict: defaults < ``inputs`` < ``effect``. ``None`` and ``""``
    in the feature never override a default (an emptied field means "use the default")."""
    f = feature_dict(feature)
    out = {row["name"]: row.get("default") for row in schema}
    for source in (f.get("inputs") or {}, f.get("effect") or {}):
        for k, v in source.items():
            if v is None or v == "":
                continue
            out[k] = v
    return out


def num(value: Any, default: float = 0.0) -> float:
    """Float or ``default``; never raises on junk from a page."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def count_of(feature: Any) -> int:
    f = feature_dict(feature)
    try:
        return max(int(f.get("count") or 1), 1)
    except (TypeError, ValueError):
        return 1


def product_of(feature: Any) -> dict[str, Any]:
    return feature_dict(feature).get("product") or {}


def basin_plan(basin: dict[str, Any] | None) -> dict[str, float]:
    """Plan dimensions of a schema-2 basin in feet: length, width, area, depth. A round basin
    reports its diameter as both length and width."""
    b = basin or {}
    shape = str(b.get("shape") or "rectangular").lower()
    depth_ft = num(b.get("depth_in")) / 12.0
    if shape.startswith(("cyl", "round", "circ")):
        d = num(b.get("diameter_in")) / 12.0
        return {"length_ft": d, "width_ft": d, "area_sqft": 3.141592653589793 * d * d / 4.0, "depth_ft": depth_ft}
    length = num(b.get("length_in")) / 12.0
    width = num(b.get("width_in")) / 12.0
    area = num(b.get("surface_area_sqft")) or length * width
    return {"length_ft": length, "width_ft": width, "area_sqft": area, "depth_ft": depth_ft}


def site_regulated(site: dict[str, Any] | None) -> bool:
    return bool((site or {}).get("regulated"))


def flow_data(flow_fn: Any, feature: Any, derived: dict[str, Any] | None, site: Any, rules: Any) -> dict[str, Any]:
    """``derived["flow"]`` when the orchestrator passed it, else ``flow_fn``'s own data."""
    if derived and isinstance(derived.get("flow"), dict):
        return derived["flow"]
    return flow_fn(feature, site, rules).data
