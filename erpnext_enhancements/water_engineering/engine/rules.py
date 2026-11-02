"""Sapphire house rules: the design constants no public source gives.

The industry research (``docs/fountain-designer/research-industry-calculators.md`` section 7)
found no published figure for foam-jet submergence, freeboard, lighting watts per foot of
jet or weir, wind setpoints, liner allowance, spray loss, or labor. Nikolas decided on
2026-11-01 that each becomes a **named, editable constant** on Water Engineering Settings,
cited "Sapphire standard, <date>", until the designers set it.

``RULES`` is the single list of those constants. Each entry is the Settings fieldname, its
label, unit, default, the section it sits under on the Settings page, and its citation. The
Settings DocType JSON declares one field per entry with the same default, and
``tests/test_water_house_rules.py`` fails the build when the two drift.

The engine never imports frappe, so it reads the rules as a plain dict:
:func:`resolve` overlays whatever the caller passes (normally the Settings row as a dict)
on :data:`DEFAULTS`. Pass nothing and the engine runs on the defaults, which is what every
bench-free test does.

The utility rates are sourced (SLC and Rocky Mountain Power), not house rules, but they live
on the same Settings page because they change every fiscal year and the designers own them.

Labor hours ship as ``0``, which means **not set**, not "free": a budget must never be built
on a fabricated labor figure. :func:`is_unset` says so, and the budget phase flags it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HOUSE_DATE = "2026-11-01"
CIT_HOUSE = f"Sapphire standard, {HOUSE_DATE} (placeholder until the designers set it)"

CIT_SLC_WATER = "https://www.slc.gov/utilities/fy26-what-new-rates-mean/ (SLC commercial FY26, $3.53/ccf)"
CIT_RMP_POWER = (
    "https://utahnewsdispatch.com/?p=4025 (Rocky Mountain Power proposed 2026 average "
    "non-residential 10.49 c/kWh; confirm against the approved tariff)"
)
CIT_DRC_FREEBOARD = "digital-rain-curtain src/hydro/presets.js autoBasin (1.3 x drain-back rise)"
CIT_DRC_SPRAY = "digital-rain-curtain src/hydro/checks.js basinChecks (spray loss 0.5% of flow)"

# The water-moving families that carry a spray-loss factor and a labor figure. Mist is
# once-through potable water, so it has labor but no spray-loss factor.
SPRAY_FAMILIES = (
    "weir", "jets", "foam", "laminar", "interactive", "cascade", "pondless", "curtain", "aerator",
)
LABOR_FAMILIES = (*SPRAY_FAMILIES, "mist")


@dataclass(frozen=True)
class Rule:
    """One house rule: the Settings fieldname is the dict key the engine reads."""

    key: str
    label: str
    unit: str
    default: float
    section: str
    citation: str = CIT_HOUSE
    description: str = ""
    house: bool = True  # False = a sourced value that merely lives on the Settings page


def _family_label(family: str) -> str:
    return {
        "weir": "weirs and sheet falls",
        "jets": "nozzle jets",
        "foam": "foam jets",
        "laminar": "laminar jets",
        "interactive": "interactive features",
        "cascade": "cascades and streams",
        "pondless": "pondless kits",
        "curtain": "passive curtains",
        "aerator": "floating aerators",
        "mist": "misting",
    }[family]


RULES: tuple[Rule, ...] = (
    # Foam / aerating jets
    Rule(
        "foam_submergence_min_in", "Foam-jet submergence, minimum", "in", 2.0, "foam",
        description="Shallowest water over a level-sensitive foam jet when the cut sheet gives no band.",
    ),
    Rule(
        "foam_submergence_max_in", "Foam-jet submergence, maximum", "in", 4.0, "foam",
        description="Deepest water over a level-sensitive foam jet when the cut sheet gives no band.",
    ),
    Rule(
        "autofill_deadband_in", "Autofill deadband", "in", 0.5, "foam",
        description="How far the level drifts before the autofill opens; must sit inside the jet's band.",
    ),
    # Basin
    Rule(
        "freeboard_multiplier", "Freeboard multiplier", "x", 1.3, "basin",
        CIT_HOUSE + "; modeled on " + CIT_DRC_FREEBOARD,
        description="Freeboard = this x the computed drain-back level rise.",
    ),
    Rule(
        "liner_overlap_ft", "Liner overlap allowance", "ft", 2.0, "basin",
        description="Liner = length + 2 x depth + this overlap (each direction).",
    ),
    # Lighting
    Rule(
        "light_watts_per_ft_jet", "Lighting watts per foot of jet", "W/ft", 5.0, "lighting",
        description="Lamp watts per foot of display height when no fixture is picked.",
    ),
    Rule(
        "light_watts_per_ft_weir", "Lighting watts per foot of weir", "W/ft", 4.0, "lighting",
        description="Lamp watts per foot of weir or sheet length when no fixture is picked.",
    ),
    Rule(
        "light_fixture_watts", "Default fixture watts", "W", 35.0, "lighting",
        description="The fixture size used to count fixtures before a model is picked.",
    ),
    # Wind
    Rule(
        "wind_reduce_mph", "Wind control stage 1 (reduce)", "mph", 12.0, "wind",
        description="Anemometer speed at which jets drop to the reduced height.",
    ),
    Rule(
        "wind_stop_mph", "Wind control stage 2 (stop)", "mph", 20.0, "wind",
        description="Anemometer speed at which jets shut off.",
    ),
    # Aerators
    Rule(
        "aerator_max_aspect", "Aerator: pond length-to-width per unit", "ratio", 3.0, "aerator",
        description="Above this length-to-width ratio, split the horsepower over several units.",
    ),
    # Spray loss, per family
    *(
        Rule(
            f"spray_loss_pct_{fam}", f"Spray loss, {_family_label(fam)}", "% of flow", 0.5, "spray",
            CIT_HOUSE + "; starting value from " + CIT_DRC_SPRAY,
            description="Water lost to spray and splash beyond evaporation, as a share of flow.",
        )
        for fam in SPRAY_FAMILIES
    ),
    # Labor, per family and per square foot of basin (0 = not set)
    *(
        Rule(
            f"labor_hours_{fam}", f"Labor hours, {_family_label(fam)}", "h/feature", 0.0, "labor",
            description="Install hours per feature of this family. 0 means not set yet.",
        )
        for fam in LABOR_FAMILIES
    ),
    Rule(
        "labor_hours_per_sqft_basin", "Labor hours per sq ft of basin", "h/sq ft", 0.0, "labor",
        description="Basin construction hours per square foot of water surface. 0 means not set yet.",
    ),
    Rule(
        "labor_hours_per_light_fixture", "Labor hours per light fixture", "h/fixture", 0.0, "labor",
        description="Install hours per fixture. 0 means not set yet.",
    ),
    # Budget (phase G): the labor rate prices the labor hours above; contingency and range sit on
    # the Budget print under the BOM subtotal.
    Rule(
        "labor_rate_per_hr", "Labor rate", "$/h", 0.0, "budget",
        description="Billed rate per labor hour on the budget. 0 means not set yet: labor prints unpriced.",
    ),
    Rule(
        "budget_contingency_pct", "Budget contingency", "%", 10.0, "budget",
        description="Contingency added under the priced subtotal on the Budget print. 0 means none.",
    ),
    Rule(
        "budget_range_pct", "Budget range (plus or minus)", "%", 15.0, "budget",
        description="How far either way the budget may land, printed as a low and a high figure. 0 means none.",
    ),
    # Utility rates (sourced, but owned and updated by the designers)
    Rule("kwh_rate", "Electricity rate", "$/kWh", 0.1049, "utility", CIT_RMP_POWER, house=False),
    Rule("water_rate_per_ccf", "Water rate", "$/ccf", 3.53, "utility", CIT_SLC_WATER, house=False),
)

RULES_BY_KEY: dict[str, Rule] = {r.key: r for r in RULES}

DEFAULTS: dict[str, float] = {r.key: r.default for r in RULES}

# Settings fields that are not numeric rules but change how the rules are cited.
SET_ON_FIELD = "house_rules_set_on"

# The labor rules where 0 means "not set" rather than a real zero.
UNSET_ZERO_KEYS = frozenset(r.key for r in RULES if r.key.startswith("labor_hours_")) | {"labor_rate_per_hr"}
# Rules where 0 is a value the engine accepts: labor (0 = not set, see is_unset), spray
# loss and the budget's contingency and range (0 = none). Every other rule falls back to its
# default at 0 or below, because Frappe stores a cleared Float as 0 and a 0 fixture size or
# aspect ratio divides by zero.
ZERO_OK_KEYS = (
    UNSET_ZERO_KEYS
    | frozenset(r.key for r in RULES if r.key.startswith("spray_loss_pct_"))
    | {"budget_contingency_pct", "budget_range_pct"}
)


def _coerce(key: str, value: Any) -> float | None:
    """``value`` as the rule's float, or ``None`` when the default must apply: blank, junk,
    NaN, negative, or 0 for a rule where 0 is meaningless."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0 or (f == 0 and key not in ZERO_OK_KEYS):
        return None
    return f


def resolve(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The rules dict the engine reads: :data:`DEFAULTS` with ``settings`` laid over it.

    ``settings`` is normally the Water Engineering Settings row as a dict. A key that is
    missing, ``None``, ``""``, junk, negative, or 0 (outside :data:`ZERO_OK_KEYS`) keeps the
    default; anything else is coerced to float. Nothing here raises, because a bad Settings
    value must never take the designer page down. ``house_rules_set_on`` passes through.
    """
    out: dict[str, Any] = dict(DEFAULTS)
    out[SET_ON_FIELD] = None
    for key, value in (settings or {}).items():
        if key == SET_ON_FIELD:
            out[key] = str(value) if value else None
            continue
        if key not in RULES_BY_KEY:
            continue
        f = _coerce(key, value)
        if f is not None:
            out[key] = f
    return out


def rule(rules: dict[str, Any] | None, key: str) -> float:
    """One rule's value from a resolved (or partial, or absent) rules dict, with the same
    fallbacks as :func:`resolve` (so a raw Settings dict is safe here too)."""
    f = _coerce(key, (rules or {}).get(key))
    return DEFAULTS[key] if f is None else f


def citation(key: str, rules: dict[str, Any] | None = None) -> str:
    """How to cite rule ``key``. A sourced rule cites its source. A house rule cites
    "Sapphire standard, <date>", the placeholder text until the designers record the date
    they set the rules (``house_rules_set_on``)."""
    spec = RULES_BY_KEY[key]
    if not spec.house:
        return spec.citation
    set_on = (rules or {}).get(SET_ON_FIELD)
    if set_on:
        return f"Sapphire standard, {set_on}"
    return spec.citation


def is_unset(rules: dict[str, Any] | None, key: str) -> bool:
    """True when a labor rule still holds its 0 = "not set" default."""
    return key in UNSET_ZERO_KEYS and rule(rules, key) == 0.0


def house_rule_rows(rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every rule as a row for the page's *House rules* panel."""
    resolved = resolve(rules) if rules is None or SET_ON_FIELD not in rules else rules
    return [
        {
            "key": r.key,
            "label": r.label,
            "unit": r.unit,
            "value": rule(resolved, r.key),
            "default": r.default,
            "section": r.section,
            "house": r.house,
            "citation": citation(r.key, resolved),
            "description": r.description,
        }
        for r in RULES
    ]
