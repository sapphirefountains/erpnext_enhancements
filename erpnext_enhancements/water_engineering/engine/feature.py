"""Water-feature flow requirements: weirs/slots (Francis), nozzle arrays, and
orifice nozzles driven by a Nozzle Profile catalog.

Verified against DOC-0049 ``I - Weir``:
    Q_gpm = (36 * L_ft * h_in^1.5) - (0.3 * n * h_in^2.5)   (n = end contractions)

The SUPPORT ``WeirInfo`` "GPM per foot" table is this same formula evaluated at
L=1, n=2 -- so we compute Francis directly rather than store a lookup that could
drift.

Orifice nozzles: the orifice equation Q = Cd*A*sqrt(2gh) is textbook physics, but
the discharge coefficient and orifice size are NOT in the Sapphire source docs —
they come from a ``Nozzle Profile`` (manufacturer cut sheet). :func:`nozzle_flow`
computes from those sourced coefficients (Cd + orifice area/diameter), or scales a
rated GPM by sqrt(head); with neither it returns a clear "needs a Nozzle Profile"
warning rather than an invented number.
"""

from __future__ import annotations

import math

from .constants import (
    CIT_WEIR,
    DEFAULT_WEIR_CONTRACTIONS,
    WEIR_FRANCIS_COEFF,
    WEIR_FRANCIS_CONTRACTION_COEFF,
)
from .envelope import CalcResult, make_input

CIT_ORIFICE = "Orifice equation Q=Cd*A*sqrt(2gh) (textbook); coefficients from Nozzle Profile catalog"


def weir_flow(
    length_ft: float,
    head_in: float,
    contractions: int = DEFAULT_WEIR_CONTRACTIONS,
) -> CalcResult:
    """Flow (GPM) over a weir / slot / vanishing edge via the Francis formula."""
    length_ft = float(length_ft)
    head_in = float(head_in)
    q = (
        WEIR_FRANCIS_COEFF * length_ft * head_in**1.5
        - WEIR_FRANCIS_CONTRACTION_COEFF * contractions * head_in**2.5
    )
    warnings = []
    if q < 0:
        q = 0.0
        warnings.append("Francis formula went negative (head too small for this length); clamped to 0.")
    return CalcResult(
        calc="weir_flow",
        value=q,
        unit="GPM",
        inputs={
            "length": make_input(length_ft, "ft", "user", "I - Weir!C12"),
            "head": make_input(head_in, "in", "user", "I - Weir!C14"),
            "contractions": make_input(contractions, "", "user", "I - Weir!C15 (default 2)"),
        },
        formula="Q_gpm = (36 * L_ft * h_in^1.5) - (0.3 * n * h_in^2.5)",
        steps=[
            f"Q = (36 * {length_ft} * {head_in}^1.5) - (0.3 * {contractions} * {head_in}^2.5)",
            f"Q = {q:.4f} GPM",
        ],
        citations=[CIT_WEIR],
        warnings=warnings,
    )


def nozzle_array_flow(nozzle_count: int, gpm_each: float) -> CalcResult:
    """Total flow (GPM) for an array of identical nozzles at a known per-nozzle rate."""
    nozzle_count = int(nozzle_count or 0)
    gpm_each = float(gpm_each or 0.0)
    q = nozzle_count * gpm_each
    return CalcResult(
        calc="nozzle_array_flow",
        value=q,
        unit="GPM",
        inputs={
            "nozzle_count": make_input(nozzle_count, "", "user"),
            "gpm_each": make_input(gpm_each, "GPM", "user", "manufacturer cut sheet"),
        },
        formula="Q_gpm = nozzle_count * gpm_each",
        steps=[f"Q = {nozzle_count} * {gpm_each} = {q:.4f} GPM"],
    )


def nozzle_flow(
    head_ft: float = 0.0,
    *,
    cd: float | None = None,
    orifice_area_in2: float | None = None,
    orifice_diameter_in: float | None = None,
    rated_gpm: float | None = None,
    rated_head_ft: float | None = None,
    nozzle_profile: str = "",
) -> CalcResult:
    """Orifice nozzle flow (GPM) from a Nozzle Profile's sourced coefficients.

    Method 1 (Cd + orifice): Q = Cd * A * sqrt(2 g h), A from orifice area or
    diameter (A = pi/4 * d^2). Method 2 (rated): Q = rated_gpm * sqrt(h / h_rated)
    — orifice flow scales with the square root of head. ``head_ft`` is the nozzle
    supply head. With neither method's coefficients, returns a warning stub.
    """
    head_ft = float(head_ft or 0)
    area = None
    if orifice_area_in2:
        area = float(orifice_area_in2)
    elif orifice_diameter_in:
        area = math.pi / 4 * float(orifice_diameter_in) ** 2

    if cd and area and head_ft > 0:
        g = 9.80665  # m/s^2
        h_m = head_ft * 0.3048
        area_m2 = area * 0.00064516  # in^2 -> m^2
        q = float(cd) * area_m2 * math.sqrt(2 * g * h_m) * 15850.32314  # m^3/s -> US GPM
        return CalcResult(
            calc="nozzle_flow",
            value=q,
            unit="GPM",
            inputs={
                "supply_head": make_input(head_ft, "ft", "user"),
                "cd": make_input(cd, "", "lookup", "Nozzle Profile"),
                "orifice_area": make_input(round(area, 4), "in^2", "lookup", "Nozzle Profile"),
                "nozzle_profile": make_input(nozzle_profile, "", "user"),
            },
            formula="Q = Cd * A * sqrt(2 g h)",
            steps=[
                f"A = {area:.4f} in^2 ; h = {head_ft} ft",
                f"Q = {cd} * A * sqrt(2*9.80665*{h_m:.4f}) = {q:.4f} GPM",
            ],
            citations=[CIT_ORIFICE],
        )

    if rated_gpm and rated_head_ft and head_ft > 0:
        q = float(rated_gpm) * math.sqrt(head_ft / float(rated_head_ft))
        return CalcResult(
            calc="nozzle_flow",
            value=q,
            unit="GPM",
            inputs={
                "supply_head": make_input(head_ft, "ft", "user"),
                "rated_gpm": make_input(rated_gpm, "GPM", "lookup", "Nozzle Profile"),
                "rated_head": make_input(rated_head_ft, "ft", "lookup", "Nozzle Profile"),
                "nozzle_profile": make_input(nozzle_profile, "", "user"),
            },
            formula="Q = rated_gpm * sqrt(head / rated_head)",
            steps=[f"Q = {rated_gpm} * sqrt({head_ft}/{rated_head_ft}) = {q:.4f} GPM"],
            citations=[CIT_ORIFICE],
        )

    return CalcResult(
        calc="nozzle_flow",
        value=None,
        unit="GPM",
        inputs={
            "supply_head": make_input(head_ft, "ft", "user"),
            "nozzle_profile": make_input(nozzle_profile, "", "user"),
        },
        formula="Q = Cd * A * sqrt(2 g h)  (needs a Nozzle Profile + supply head)",
        warnings=[
            "Orifice nozzle flow needs a Nozzle Profile (discharge coefficient + "
            "orifice size, or a rated GPM @ head) and a positive supply head. Pick a "
            "Nozzle Profile, enter the supply head, or use a weir / nozzle_array_flow "
            "with a rated GPM.",
        ],
    )
