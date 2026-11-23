"""Annual energy and cost per load from its watts and daily hours.

``workbook.electric_cost`` stays the DOC-0049 "E - Elec Costs" figure for the
pumps (WHP -> BHP -> kW from flow and TDH); this module covers every load in the
list and is the number the O&M rollup should read. The two agree when the pump
watts here come from the same hydraulic power.
"""

from __future__ import annotations

from ..constants import DAYS_PER_YEAR, DEFAULT_KWH_RATE
from ..envelope import CalcResult, make_input
from ._util import num

CIT_ENERGY = "kWh = kW x hours/day x days/year (site utility rate); DOC-0049 / E - Elec Costs for pumps"


def annual_energy(
    loads: list[dict] | None,
    rate_per_kwh: float = DEFAULT_KWH_RATE,
    days_per_year: float = DAYS_PER_YEAR,
) -> CalcResult:
    """Annual kWh and cost per load and in total. ``value`` is the total $/yr;
    ``inputs['rows']`` lists ``{label, kw, hours_day, kwh_yr, cost_yr}``."""
    rate = num(rate_per_kwh, DEFAULT_KWH_RATE)
    days = num(days_per_year, DAYS_PER_YEAR)
    rows = []
    for r in loads or []:
        kw = num(r.get("watts")) * int(num(r.get("qty"), 1)) / 1000.0
        hours = min(24.0, num(r.get("duty_hours_day"), 24.0))
        kwh = kw * hours * days
        rows.append({
            "label": r.get("label"),
            "kind": r.get("kind"),
            "kw": round(kw, 4),
            "hours_day": hours,
            "kwh_yr": round(kwh, 1),
            "cost_yr": round(kwh * rate, 2),
        })
    total_kwh = sum(x["kwh_yr"] for x in rows)
    total = round(sum(x["cost_yr"] for x in rows), 2)
    return CalcResult(
        calc="annual_energy",
        value=total,
        unit="$/yr",
        inputs={
            "rate_per_kwh": make_input(rate, "$/kWh", "user"),
            "days_per_year": make_input(days, "days", "default"),
            "total_kwh": make_input(round(total_kwh, 1), "kWh", "prior_calc"),
            "rows": make_input(rows, "", "prior_calc"),
        },
        formula="kWh/yr = sum(kW x hours/day) x days ; $ = kWh x rate",
        steps=[f"{x['label']}: {x['kw']:g} kW x {x['hours_day']:g} h x {days:g} d = {x['kwh_yr']:,.0f} kWh"
               for x in rows] + [f"total {total_kwh:,.0f} kWh x ${rate:g} = ${total:,.2f}/yr"],
        citations=[CIT_ENERGY],
        warnings=["Uses each load's running watts; a VFD pump at reduced speed draws less (affinity laws)."],
    )


__all__ = ["annual_energy"]
