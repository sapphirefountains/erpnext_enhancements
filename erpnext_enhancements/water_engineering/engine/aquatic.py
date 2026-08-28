"""Regulated-aquatic-venue flows: turnover time, minimum circulation, bather
load, skimmer sizing, and reported main-drain open-area flow.

Health-department-regulated pools and spas are sized by rules the decorative-
fountain spine (turnovers/hr) does not use: a MAXIMUM turnover *time* (minutes),
a minimum circulation flow derived from it, a bather load from water-surface
area, and skimmer counts/flows keyed to surface area. These functions are the
code-parameterized regulated path; the fountain-era rollup in
``workbook.program_rules`` (fixed 9 SF/user, 400 SF/skimmer) is left untouched so
its golden stays green — the two coexist and are unified later only with
re-pinned goldens.

Numerically verified against the Fika Reflexology Spa health-department submittal
(Salt Lake County Health Dept, 2023 — Michael Madsen, P.E.):

    volume 425 gal, design flow 35 GPM  -> turnover 425/35 = 12.14 min
    minimum flow  425 / (0.5 * 60 min)  = 14.17 GPM
    bather load   46 SF / 10 SF-person  ~ 5 (raw 4.6)
    skimmer       ceil(46/100) = 1 ; 63 GPM rated * 80% = 50.4 GPM
    main drain    1.5 ft/s * 9.02 in^2  = 42 GPM (one drain; the VGB gate is separate)

The bather-load divisor and integer-rounding convention vary by governing code
(the Fika calc sheet shows 46/10 in the arithmetic yet 6 in the data block), so
both are parameters and the raw ratio + any rounding disagreement are surfaced
rather than a single number being hard-coded. Circulation (``turnover_gpm``),
VGB anti-entrapment (``suction_outlet_vgb``), therapy jets (``nozzle_array_flow``),
filtration (``filtration_area``), heating (``heating_load``) and chemistry reuse
the existing engine functions unchanged.
"""

from __future__ import annotations

import math

from .constants import (
    AQUATIC_MAX_TURNOVER_MIN,
    AQUATIC_SF_PER_BATHER,
    CFS_TO_GPM,
    CIT_AQUATIC,
    SKIMMER_DERATE,
    SKIMMER_SF_EACH,
    VGB_MAX_COVER_VELOCITY_FPS,
)
from .envelope import CalcResult, make_input

# Venue-type labels that are decorative fountains (NOT health-dept regulated).
# Blank counts as a fountain so the existing default path stays unregulated.
_FOUNTAIN_VENUES = frozenset({"", "decorative fountain", "interactive water feature", "fountain"})

# Full venue-type label -> constant-table family key.
_VENUE_FAMILY = {
    "commercial spa": "spa",
    "spa": "spa",
    "commercial pool": "pool",
    "pool": "pool",
    "wading pool": "wading",
    "wading": "wading",
    "therapy pool": "therapy",
    "therapy": "therapy",
}


def is_regulated(venue_type: str) -> bool:
    """True when a venue is a health-department-regulated pool/spa (not a fountain).

    Blank or fountain venue types return ``False`` so the decorative-fountain path
    stays the default. This is the single branch predicate the spine, the doctype
    controller, and ``issues.py`` share.
    """
    return (venue_type or "").strip().lower() not in _FOUNTAIN_VENUES


def venue_family(venue_type: str) -> str:
    """Map a venue-type label to a constant-table family key.

    Returns one of ``spa`` / ``pool`` / ``wading`` / ``therapy``; unknown labels
    fall back to ``spa`` (the most conservative turnover).
    """
    return _VENUE_FAMILY.get((venue_type or "").strip().lower(), "spa")


def _round_capacity(raw: float, mode: str) -> int:
    """Integer bather-load rounding by convention (floor is the conservative default)."""
    if mode == "ceil":
        return math.ceil(raw)
    if mode in ("round", "nearest"):
        return int(round(raw))
    return math.floor(raw)


def turnover_time(volume_gal: float, flow_gpm: float) -> CalcResult:
    """Turnover time (minutes) = water volume / circulation flow."""
    vol = float(volume_gal)
    flow = float(flow_gpm)
    inputs = {
        "volume": make_input(vol, "gal", "prior_calc", "basin_volume"),
        "flow": make_input(flow, "GPM", "user"),
    }
    if flow <= 0 or vol < 0:
        return CalcResult(
            calc="turnover_time",
            unit="min",
            inputs=inputs,
            formula="turnover_min = volume_gal / flow_gpm",
            citations=[CIT_AQUATIC],
            warnings=["Circulation flow must be > 0 (and volume >= 0) to compute a turnover time."],
        )
    minutes = vol / flow
    return CalcResult(
        calc="turnover_time",
        value=minutes,
        unit="min",
        inputs=inputs,
        formula="turnover_min = volume_gal / flow_gpm",
        steps=[f"turnover = {vol:g} / {flow:g} = {minutes:.2f} min"],
        citations=[CIT_AQUATIC],
    )


def minimum_flow_rate(
    volume_gal: float,
    max_turnover_min: float = 0.0,
    venue: str = "spa",
    governing_code: str = "",
) -> CalcResult:
    """Minimum circulation flow (GPM) = volume / the code's maximum turnover time.

    Spas / wading / therapy pools turn over in 30 min; a general pool's maximum
    turnover time is longer. ``max_turnover_min`` overrides the family default.
    Arithmetically this equals ``turnover_gpm(volume, 60 / max_turnover_min)``.
    """
    vol = float(volume_gal)
    fam = venue_family(venue)
    max_min = float(max_turnover_min or 0) or AQUATIC_MAX_TURNOVER_MIN.get(fam, 30.0)
    if vol < 0 or max_min <= 0:
        return CalcResult(
            calc="minimum_flow_rate",
            unit="GPM",
            formula="min_flow_gpm = volume_gal / max_turnover_min",
            citations=[CIT_AQUATIC],
            warnings=["Volume must be >= 0 and the maximum turnover time > 0."],
        )
    gpm = vol / max_min
    return CalcResult(
        calc="minimum_flow_rate",
        value=gpm,
        unit="GPM",
        inputs={
            "volume": make_input(vol, "gal", "prior_calc", "basin_volume"),
            "max_turnover_min": make_input(max_min, "min", "standard", CIT_AQUATIC),
        },
        formula="min_flow_gpm = volume_gal / max_turnover_min",
        steps=[
            f"min_flow = {vol:g} / {max_min:g} = {gpm:.2f} GPM",
            f"(equivalent to turnover_gpm at {60.0 / max_min:g} turnovers/hr)",
        ],
        citations=[CIT_AQUATIC] + ([governing_code] if governing_code else []),
    )


def bather_load(
    surface_area_sf: float,
    sf_per_person: float = 0.0,
    rounding: str = "floor",
    venue: str = "spa",
    governing_code: str = "",
) -> CalcResult:
    """Maximum bather load = water-surface area / area-per-person, integer-rounded.

    The divisor varies by governing code (spa ~10 SF, pool ~15 SF); pass
    ``sf_per_person`` to override the family default. Because codes differ on
    whether to round down / to nearest / up, the raw ratio is shown and a warning
    is raised when the conventions disagree.
    """
    sa = float(surface_area_sf)
    fam = venue_family(venue)
    per = float(sf_per_person or 0) or AQUATIC_SF_PER_BATHER.get(fam, 10.0)
    if sa <= 0 or per <= 0:
        return CalcResult(
            calc="bather_load",
            unit="bathers",
            formula="bather_load = round(surface_area_sf / sf_per_person)",
            citations=[CIT_AQUATIC],
            warnings=["Surface area and area-per-person must be > 0."],
        )
    raw = sa / per
    mode = (rounding or "floor").strip().lower()
    value = _round_capacity(raw, mode)
    warnings: list[str] = []
    if math.floor(raw) != math.ceil(raw):
        warnings.append(
            f"Bather-load raw = {raw:.2f}; codes differ on rounding "
            f"(floor {math.floor(raw)} / nearest {int(round(raw))} / ceil {math.ceil(raw)}) — "
            f"using '{mode}' = {value}. Confirm the governing code's rule."
        )
    return CalcResult(
        calc="bather_load",
        value=value,
        unit="bathers",
        inputs={
            "surface_area_sf": make_input(sa, "SF", "user"),
            "sf_per_person": make_input(per, "SF/person", "standard", CIT_AQUATIC),
            "rounding": make_input(mode, "", "user"),
        },
        formula="bather_load = floor|nearest|ceil(surface_area_sf / sf_per_person)",
        steps=[
            f"raw = {sa:g} / {per:g} = {raw:.2f}",
            f"bather_load = {mode}({raw:.2f}) = {value}",
        ],
        citations=[CIT_AQUATIC] + ([governing_code] if governing_code else []),
        warnings=warnings,
    )


def skimmer_sizing(
    surface_area_sf: float,
    sf_each: float = 0.0,
    rated_gpm: float = 0.0,
    derate: float = SKIMMER_DERATE,
    venue: str = "spa",
    governing_code: str = "",
) -> CalcResult:
    """Skimmer count = ceil(surface area / area-per-skimmer); each skimmer is
    sized to operate at ``derate`` (80%) of its rated GPM."""
    sa = float(surface_area_sf)
    fam = venue_family(venue)
    each_sf = float(sf_each or 0) or SKIMMER_SF_EACH.get(fam, 100.0)
    if sa <= 0 or each_sf <= 0:
        return CalcResult(
            calc="skimmer_sizing",
            unit="skimmers",
            formula="count = ceil(surface_area_sf / sf_each)",
            citations=[CIT_AQUATIC],
            warnings=["Surface area and area-per-skimmer must be > 0."],
        )
    count = math.ceil(sa / each_sf)
    rated = float(rated_gpm or 0)
    flow_each = rated * float(derate)
    steps = [f"skimmer count = ceil({sa:g} / {each_sf:g}) = {count}"]
    warnings: list[str] = []
    if rated > 0:
        steps.append(f"operating flow each = {rated:g} * {derate:g} = {flow_each:.1f} GPM")
    else:
        warnings.append("No rated skimmer GPM given; operating (80%) flow not computed.")
    return CalcResult(
        calc="skimmer_sizing",
        value=count,
        unit="skimmers",
        inputs={
            "surface_area_sf": make_input(sa, "SF", "user"),
            "sf_each": make_input(each_sf, "SF/skimmer", "standard", CIT_AQUATIC),
            "rated_gpm": make_input(rated, "GPM", "lookup"),
            "derate": make_input(float(derate), "", "standard", CIT_AQUATIC),
        },
        formula="count = ceil(SA / sf_each) ; operating_flow_each = rated_gpm * derate",
        steps=steps,
        citations=[CIT_AQUATIC] + ([governing_code] if governing_code else []),
        warnings=warnings,
    )


def main_drain_flow(
    open_area_in2: float,
    velocity_fps: float = VGB_MAX_COVER_VELOCITY_FPS,
    drains: int = 2,
    governing_code: str = "",
) -> CalcResult:
    """Reported open-area flow (GPM) one main-drain cover carries at the maximum
    approach velocity: ``Q = velocity * (open_area_in2 / 144) * 448.8``.

    This is the schedule/report line ("drain capacity at 1.5 ft/s"); the safety
    go/no-go is the separate VGB anti-entrapment gate ``suction_outlet_vgb``.
    """
    area = float(open_area_in2)
    v = float(velocity_fps)
    n = int(drains or 1)
    if area <= 0 or v <= 0:
        return CalcResult(
            calc="main_drain_flow",
            unit="GPM",
            formula="per_drain_gpm = velocity_fps * (open_area_in2 / 144) * 448.8",
            citations=[CIT_AQUATIC],
            warnings=["Open area and approach velocity must be > 0."],
        )
    per_drain = v * (area / 144.0) * CFS_TO_GPM
    total = per_drain * n
    return CalcResult(
        calc="main_drain_flow",
        value=per_drain,
        unit="GPM",
        inputs={
            "open_area_in2": make_input(area, "in^2", "lookup"),
            "velocity_fps": make_input(v, "ft/s", "standard", "VGB max cover velocity 1.5 ft/s"),
            "drains": make_input(n, "", "user"),
        },
        formula="per_drain_gpm = velocity_fps * (open_area_in2 / 144) * 448.8",
        steps=[
            f"per drain = {v:g} * ({area:g}/144) * 448.8 = {per_drain:.1f} GPM",
            f"{n} drains, total open-area flow = {total:.1f} GPM "
            "(each cover must carry the full system flow with one drain blocked)",
        ],
        citations=[CIT_AQUATIC] + ([governing_code] if governing_code else []),
        warnings=[
            "Open-area flow only; not a substitute for a listed cover's stamped "
            "flow rating — run suction_outlet_vgb for the anti-entrapment gate."
        ],
    )
