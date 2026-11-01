"""Feature-family rule modules for the Fountain Designer (schema 2).

Two kinds of module, listed in ``README.md`` beside this file:

* **Feature families** (:data:`FAMILIES`), one per kind of water feature. Each exposes
  ``inputs()``, ``flow(feature, site, rules)``, ``checks(feature, derived, site, rules)`` and
  ``terminal(feature, derived)``; see :mod:`.base`.
* **System modules** (:data:`SYSTEM_MODULES`): lighting, winterization and heat, which read
  the whole model. Each exposes ``inputs()``, ``calc(model, derived, rules)`` and
  ``checks(model, derived, rules)``.

Every module is stdlib-only and never imports frappe or ``engine.hydraulics``.
"""

from __future__ import annotations

from typing import Any

from . import (
    aerator,
    cascade,
    curtain,
    foam,
    heat,
    interactive,
    jets,
    laminar,
    lighting,
    mist,
    pondless,
    weir,
    winterization,
)
from .base import BLOCKER, INFO, PASS, SEVERITIES, WARNING, Check, FamilyResult

FAMILIES: dict[str, Any] = {
    m.FAMILY: m
    for m in (weir, jets, foam, laminar, interactive, cascade, pondless, curtain, mist, aerator)
}
SYSTEM_MODULES: dict[str, Any] = {m.FAMILY: m for m in (lighting, winterization, heat)}

# Old (schema 1) feature types and the family each now belongs to. Matching is exact first,
# then by keyword, so a type the list does not know still lands somewhere sensible.
_KEYWORDS = (
    ("splash", "interactive"), ("interactive", "interactive"), ("dry deck", "interactive"),
    ("laminar", "laminar"), ("foam", "foam"), ("geyser", "foam"), ("aerat", "aerator"),
    ("float", "aerator"), ("mist", "mist"), ("fog", "mist"), ("curtain", "curtain"), ("lace", "curtain"),
    ("screen", "curtain"), ("pondless", "pondless"), ("urn", "pondless"), ("bubbler", "pondless"),
    ("tier", "cascade"), ("cascade", "cascade"), ("stream", "cascade"), ("waterfall", "cascade"),
    ("rill", "cascade"), ("pond", "cascade"), ("weir", "weir"), ("wall", "weir"), ("vanish", "weir"),
    ("slot", "weir"), ("spill", "weir"), ("sheet", "weir"), ("nozzle", "jets"), ("jet", "jets"),
    ("orifice", "jets"), ("array", "jets"), ("ring", "jets"),
)


def family_for_type(feature_type: str) -> str:
    """The family for a feature type name (schema 1's ``feature_type`` or any label)."""
    t = (feature_type or "").strip()
    for key, module in FAMILIES.items():
        if t in module.TYPES:
            return key
    low = t.lower()
    for word, family in _KEYWORDS:
        if word in low:
            return family
    return "jets"


def get(family: str) -> Any:
    """The family module, or ``None`` for an unknown family."""
    return FAMILIES.get(family) or SYSTEM_MODULES.get(family)


def catalog() -> list[dict[str, Any]]:
    """Every family with its label, types and input schema: what ``boot()`` sends the page."""
    rows = [
        {"family": k, "label": m.LABEL, "types": list(m.TYPES), "inputs": m.inputs(), "system": False}
        for k, m in FAMILIES.items()
    ]
    rows += [
        {"family": k, "label": m.LABEL, "types": [], "inputs": m.inputs(), "system": True}
        for k, m in SYSTEM_MODULES.items()
    ]
    return rows


def run_feature(feature: dict[str, Any], site: dict[str, Any] | None = None,
                rules: dict[str, Any] | None = None, derived: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flow, checks and terminal for one feature, as JSON-ready dicts. ``derived`` extras
    (``basin``, ``manifold_spread_pct``, ...) pass through to ``checks``."""
    module = FAMILIES.get((feature or {}).get("family"))
    if module is None:
        return {"flow": None, "checks": [Check("UNKNOWN_FAMILY", BLOCKER, "Unknown feature family",
                                                detail=str((feature or {}).get("family")),
                                                feature=(feature or {}).get("id") or "").to_dict()],
                "terminal": None}
    res = module.flow(feature, site, rules)
    ctx = dict(derived or {}, flow=res.data)
    return {
        "flow": res.to_dict(),
        "checks": [c.to_dict() for c in module.checks(feature, ctx, site, rules)],
        "terminal": module.terminal(feature, ctx),
    }


__all__ = [
    "BLOCKER",
    "FAMILIES",
    "INFO",
    "PASS",
    "SEVERITIES",
    "SYSTEM_MODULES",
    "WARNING",
    "Check",
    "FamilyResult",
    "catalog",
    "family_for_type",
    "get",
    "run_feature",
]
