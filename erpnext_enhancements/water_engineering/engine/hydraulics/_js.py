"""JavaScript number semantics the port must reproduce to match the DRC goldens.

The Digital Rain Curtain hydraulics are JavaScript, and Python disagrees with it in
small ways that move a golden past 1e-12 or turn a NaN into a number. Each helper
here is the JS behaviour, named after it. The list comes from Aquafect's Rust port
(``aquafect/docs/plan/01-hydro-port.md``, "Decisions made while porting"):

* ``Math.max`` / ``Math.min`` return NaN when any argument is NaN; Python's
  ``max()`` / ``min()`` return whichever argument the comparison happens to keep.
* ``x || d`` treats 0, NaN, '' and null as missing; Python's ``x or d`` keeps NaN.
* ``Number.isFinite`` is false for booleans and strings; ``math.isfinite(True)``
  is True.
* ``toFixed`` rounds ties away from zero on the exact binary value; Python's
  ``round()`` and ``format`` round half to even.
* ``**`` overflows to Infinity and ``0 ** -1`` is Infinity; Python raises.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

NAN = float("nan")
INF = float("inf")


def jmax(*xs: float) -> float:
    """``Math.max``: NaN if any argument is NaN, -Infinity with no arguments."""
    out = -INF
    for x in xs:
        if x != x:
            return NAN
        if x > out:
            out = x
    return out


def jmin(*xs: float) -> float:
    """``Math.min``: NaN if any argument is NaN, Infinity with no arguments."""
    out = INF
    for x in xs:
        if x != x:
            return NAN
        if x < out:
            out = x
    return out


def or_(x, default):
    """``x || default``: 0, NaN, None, False and '' take the default."""
    if x is None or x is False or x == "" or (isinstance(x, (int, float)) and (x != x or x == 0)):
        return default
    return x


def is_number(x) -> bool:
    """``Number.isFinite(x)``: a finite int or float, never a bool or a string."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def num(x) -> float:
    """``+x`` for the values a project field can hold (None -> 0, '' -> 0, bad text -> NaN)."""
    if x is None or x == "":
        return 0.0
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return NAN


def jpow(a: float, b: float) -> float:
    """``a ** b`` with JS results where Python raises or goes complex."""
    try:
        r = a**b
    except OverflowError:
        return INF if a > 0 or (float(b).is_integer() and b % 2 == 0) else -INF
    except ZeroDivisionError:
        return INF
    if isinstance(r, complex):
        return NAN
    return r


def jlog(x: float) -> float:
    """``Math.log``: NaN below zero, -Infinity at zero."""
    if x != x or x < 0:
        return NAN
    if x == 0:
        return -INF
    return math.log(x)


def jlog10(x: float) -> float:
    """``Math.log10``: NaN below zero, -Infinity at zero."""
    if x != x or x < 0:
        return NAN
    if x == 0:
        return -INF
    return math.log10(x)


def jsqrt(x: float) -> float:
    """``Math.sqrt``: NaN below zero."""
    if x != x or x < 0:
        return NAN
    return math.sqrt(x)


def to_fixed(x: float, digits: int) -> float:
    """``+x.toFixed(digits)``: ties away from zero, decided on the exact binary value."""
    if not math.isfinite(x):
        return x
    q = Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return float(q)
