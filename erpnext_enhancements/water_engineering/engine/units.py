"""Unit conversions used across the engine (all verified constants)."""

from __future__ import annotations

from .constants import FT_PER_PSI, GAL_PER_CUBIC_INCH, LB_PER_GAL


def cubic_inches_to_gallons(cubic_inches: float) -> float:
    """in^3 -> US gallons (DOC-0048 Basin!J factor 0.004329)."""
    return float(cubic_inches) * GAL_PER_CUBIC_INCH


def gallons_to_pounds(gallons: float) -> float:
    """US gallons of water -> pounds (DOC-0048 Basin!K factor 8.34)."""
    return float(gallons) * LB_PER_GAL


def feet_to_psi(feet_of_head: float) -> float:
    """Feet of head -> psi (fresh water; 2.31 ft/psi, engineering standard)."""
    return float(feet_of_head) / FT_PER_PSI


def psi_to_feet(psi: float) -> float:
    """psi -> feet of head (fresh water; 2.31 ft/psi, engineering standard)."""
    return float(psi) * FT_PER_PSI
