"""Small shared helpers for the electrical package (not part of the public API)."""

from __future__ import annotations

import math

from ..data.nec_tables import STD_AMPS_240_6, VERIFY_EDITION

SQRT3 = math.sqrt(3.0)
# Floating-point slack for "at least" comparisons, so 28 A x 2.5 = 70.0000001
# still lands on the 70 A breaker it equals.
EPS = 1e-9


def num(value, default: float = 0.0) -> float:
    """``float(value)`` that treats None / "" / junk as ``default``."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def phase_of(value, default: int = 1) -> int:
    """1 or 3 (anything that is not 3 is single-phase; "2" phase does not exist here)."""
    try:
        return 3 if int(num(value, default)) == 3 else 1
    except (TypeError, ValueError):
        return default


def phase_mult(phase: int) -> float:
    """VA = V x A x this: 1 for single-phase, sqrt(3) for three-phase."""
    return SQRT3 if phase_of(phase) == 3 else 1.0


def va_of(amps: float, voltage: float, phase: int) -> float:
    return num(amps) * num(voltage) * phase_mult(phase)


def amps_of(va: float, voltage: float, phase: int) -> float:
    v = num(voltage)
    return num(va) / (v * phase_mult(phase)) if v > 0 else 0.0


def next_standard(amps: float, sizes=STD_AMPS_240_6) -> int:
    """Smallest standard rating at or above ``amps`` (240.6(A))."""
    a = num(amps)
    return next((s for s in sizes if s >= a - EPS), math.ceil(a))


def standard_not_exceeding(amps: float, sizes=STD_AMPS_240_6) -> int:
    """Largest standard rating at or below ``amps``; the smallest size if none is."""
    a = num(amps)
    return next((s for s in reversed(sizes) if s <= a + EPS), sizes[0])


def poles_for(voltage: float, phase: int) -> int:
    """Breaker poles: 3 for three-phase, 2 for a line-to-line single-phase load
    (208/240 V), 1 for a 120 V load."""
    if phase_of(phase) == 3:
        return 3
    return 2 if num(voltage) > 150 else 1


def edition_warning() -> str:
    return VERIFY_EDITION
