"""Water-feature flow requirements: weirs/slots (Francis), nozzle arrays, and a
deliberately-stubbed orifice nozzle calc.

Verified against DOC-0049 ``I - Weir``:
    Q_gpm = (36 * L_ft * h_in^1.5) - (0.3 * n * h_in^2.5)   (n = end contractions)

The SUPPORT ``WeirInfo`` "GPM per foot" table is this same formula evaluated at
L=1, n=2 -- so we compute Francis directly rather than store a lookup that could
drift.

IMPORTANT: the orifice form Q = Cd*A*sqrt(2gh) is NOT in the Sapphire source
documents. DOC-0048 enters feature flow rate manually and there is no Cd lookup.
:func:`nozzle_flow` therefore returns no value and a clear warning until a Nozzle
Profile catalog (Cd / GPM-vs-pressure from manufacturer cut sheets) exists.
"""

from __future__ import annotations

from .constants import (
    CIT_WEIR,
    DEFAULT_WEIR_CONTRACTIONS,
    WEIR_FRANCIS_COEFF,
    WEIR_FRANCIS_CONTRACTION_COEFF,
)
from .envelope import CalcResult, make_input


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


def nozzle_flow(nozzle_profile: str = "", head_in: float = 0.0) -> CalcResult:
    """Single-orifice nozzle flow — UNSOURCED in the Sapphire docs (stub).

    Returns no value and a warning. Use :func:`nozzle_array_flow` with a rated
    GPM from the cut sheet, or supply a Nozzle Profile (Cd, area) once that
    catalog DocType exists.
    """
    return CalcResult(
        calc="nozzle_flow",
        value=None,
        unit="GPM",
        inputs={
            "nozzle_profile": make_input(nozzle_profile, "", "user"),
            "head": make_input(head_in, "in", "user"),
        },
        formula="Q = Cd * A * sqrt(2 * g * h)  (Cd/A not defined in source docs)",
        warnings=[
            "Orifice nozzle flow is not defined in the Sapphire source documents "
            "(DOC-0048 enters feature flow manually; there is no Cd lookup). Enter "
            "the rated GPM directly (nozzle_array_flow) or add a Nozzle Profile "
            "(Cd, orifice area) from the manufacturer cut sheet.",
        ],
    )
