"""Conduit fill per NEC Chapter 9: Table 1 (percent of cross section), Table 4
(conduit internal area by type and trade size) and Table 5 (THHN/THWN-2
conductor area). Over two conductors may fill 40%, two 31%, one 53%.
"""

from __future__ import annotations

from ..data.nec_tables import (
    CIT_TABLE_1,
    CIT_TABLE_4,
    CIT_TABLE_5,
    CONDUIT_AREA_IN2,
    CONDUIT_LABELS,
    FILL_PERCENT,
    THHN_AREA_IN2,
)
from ..envelope import CalcResult, make_input
from ._util import EPS, edition_warning, num

DEFAULT_CONDUIT = "PVC-40"
MIN_TRADE_SIZE = '1/2"'


def fill_percent(conductor_count: int) -> int:
    """Chapter 9 Table 1 allowance for this many conductors."""
    n = int(num(conductor_count))
    if n <= 1:
        return FILL_PERCENT[1]
    if n == 2:
        return FILL_PERCENT[2]
    return FILL_PERCENT["over_2"]


def conduit_fill(
    conductors: list[dict] | None,
    conduit_type: str = DEFAULT_CONDUIT,
    trade_size: str | None = None,
    min_size: str = MIN_TRADE_SIZE,
) -> CalcResult:
    """Smallest trade size of ``conduit_type`` (EMT, PVC-40, PVC-80, RMC) that holds
    ``conductors`` = ``[{size, qty}]`` (THHN/THWN-2, the EGC counted), or the fill
    of a given ``trade_size``. ``value`` is the trade size string."""
    ctype = conduit_type if conduit_type in CONDUIT_AREA_IN2 else DEFAULT_CONDUIT
    table = CONDUIT_AREA_IN2[ctype]
    rows = [c for c in (conductors or []) if int(num(c.get("qty"), 0)) > 0]
    count = sum(int(num(c.get("qty"))) for c in rows)
    missing = [c.get("size") for c in rows if str(c.get("size")) not in THHN_AREA_IN2]
    area = sum(THHN_AREA_IN2.get(str(c.get("size")), 0.0) * int(num(c.get("qty"))) for c in rows)
    pct = fill_percent(count)
    sizes = list(table)
    start = sizes.index(min_size) if min_size in sizes else 0
    if trade_size:
        chosen = trade_size if trade_size in table else None
    else:
        chosen = next((s for s in sizes[start:] if area <= table[s] * pct / 100.0 + EPS), None)
    warnings = []
    if missing:
        warnings.append(f"No Table 5 area for {', '.join(map(str, missing))}; those conductors were not counted.")
    if chosen is None:
        warnings.append(f"{area:.4f} in^2 of conductors exceeds the largest {CONDUIT_LABELS[ctype]} in the table.")
        return CalcResult(
            calc="conduit_fill",
            unit="trade size",
            formula="conductor area <= fill % x conduit area",
            citations=[CIT_TABLE_1, CIT_TABLE_4, CIT_TABLE_5],
            warnings=warnings,
        )
    used = 100.0 * area / table[chosen]
    if used > pct + EPS:
        warnings.append(f"{chosen} {CONDUIT_LABELS[ctype]} is {used:.1f}% full, over the {pct}% allowed.")
    warnings.append(edition_warning())
    return CalcResult(
        calc="conduit_fill",
        value=chosen,
        unit="trade size",
        inputs={
            "conduit_type": make_input(ctype, "", "user", CONDUIT_LABELS[ctype]),
            "conductor_count": make_input(count, "", "prior_calc"),
            "conductor_area_in2": make_input(round(area, 4), "in^2", "lookup", "Chapter 9 Table 5"),
            "fill_allowed_pct": make_input(pct, "%", "standard", "Chapter 9 Table 1"),
            "conduit_area_in2": make_input(table[chosen], "in^2", "lookup", "Chapter 9 Table 4"),
            "fill_pct": make_input(round(used, 2), "%", "prior_calc"),
        },
        formula="smallest trade size with sum(conductor area) <= fill % x internal area",
        steps=[
            f"{count} conductor(s): {area:.4f} in^2 ; {pct}% allowed",
            f"{chosen} {CONDUIT_LABELS[ctype]}: {table[chosen]} in^2 x {pct}% = "
            f"{table[chosen] * pct / 100:.4f} in^2 -> {used:.1f}% full",
        ],
        citations=[CIT_TABLE_1, CIT_TABLE_4, CIT_TABLE_5],
        warnings=warnings,
        status="good" if used <= pct + EPS else "bad",
    )


def conduit_for_circuit(
    size: str,
    current_carrying: int,
    egc: str | None,
    conduit_type: str = DEFAULT_CONDUIT,
    neutral: bool = False,
) -> CalcResult:
    """Conduit for one circuit: its ungrounded conductors (plus a neutral when
    used) and its EGC."""
    rows = [{"size": size, "qty": int(num(current_carrying)) + (1 if neutral else 0)}]
    if egc:
        rows.append({"size": egc, "qty": 1})
    return conduit_fill(rows, conduit_type)


__all__ = ["conduit_fill", "conduit_for_circuit", "fill_percent"]
