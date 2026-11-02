"""What the site's jurisdiction asks of a design (Fountain Designer phase J).

The rules key on the state's row of :mod:`.data.jurisdictions`, never on the code ``"UT"``: who
regulates which venues, whether Utah's interactive-feature rule applies as written, whether fog
must come from potable water, the code editions the reports cite, and the design city's climate.
Utah is one row like any other.

A site whose code the table does not have (or a blank code) has **no jurisdiction data**: the
helpers here return ``None`` or empty values, the callers warn, and the designer types the site
facts. Nothing falls back to Utah.

Stdlib only, like the rest of ``engine/``.
"""

from __future__ import annotations

import math
from typing import Any

from .data import jurisdictions as table

CODES = ("nec", "ibc", "irc", "ispsc")
CODE_LABELS = {"nec": "NEC", "ibc": "IBC", "irc": "IRC", "ispsc": "ISPSC", "mahc": "MAHC"}
# The edition this engine's own tables and citations are written to: ``data/nec_tables.py`` is NEC
# 2023, and the structural rules cite IBC 2021 (``structural/defaults.py``).
ENGINE_EDITIONS = {"nec": 2023, "ibc": 2021}
AHJ_BASES = ("local", "none")
DRY_RH_PCT = 40.0
# A dry design city when the table gives no humidity: high and hot, like the intermountain West.
DRY_ELEVATION_FT = 3000.0
DRY_AIR_F = 90.0
# The 100th meridian, the textbook line between the arid West and the humid East.
DRY_MERIDIAN = -100.0
CIT_DRY = ("Dry climate: summer design relative humidity 40% or less where known, else west of the 100th meridian "
           "(the arid-humid divide since J. W. Powell, 1878), else a design city 3,000 ft or higher with a 90 F "
           "or hotter design day (Sapphire standard, 2026-11-01)")


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    """A finite float or None (``derive._num``'s rule; the engine may not import derive)."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def code_of(site: dict | None) -> str:
    return str(_d(site).get("jurisdiction") or "").strip().upper()


def entry_for(site: dict | None) -> dict | None:
    """The table row for the site's jurisdiction, or ``None`` (no data)."""
    return table.get(code_of(site))


def state_name(site: dict | None, entry: dict | None = None) -> str:
    row = entry if entry is not None else entry_for(site)
    if row and row.get("name"):
        return str(row["name"])
    return code_of(site) or "the site's state"


def regulation(entry: dict | None) -> dict:
    return _d(_d(entry).get("regulation"))


def climate(entry: dict | None) -> dict:
    return _d(_d(entry).get("climate"))


def code(entry: dict | None, key: str) -> dict:
    return _d(_d(_d(entry).get("codes")).get(key))


def no_data_warning(site: dict | None) -> str:
    c = code_of(site)
    which = f"'{c}'" if c else "this site (no jurisdiction picked)"
    return (f"No jurisdiction data for {which}: the code editions, health rules and climate defaults are unknown. "
            "Pick the state, or type the site's elevation, design temperatures, frost depth and hardness.")


# ------------------------------------------------------------------------- code editions


def edition(entry: dict | None, key: str) -> str | None:
    """``"NEC 2023"``, ``"ISPSC not adopted"``, or ``None`` when the table has no edition."""
    c = code(entry, key)
    label = CODE_LABELS.get(key, key.upper())
    if key == "ispsc" and c.get("adopted") is False:
        return "ISPSC not adopted"
    if key == "mahc":
        status = str(c.get("status") or "")
        return {"adopted": "MAHC adopted", "referenced": "MAHC referenced", "none": "MAHC not adopted"}.get(status)
    ed = c.get("edition")
    return f"{label} {ed}" if ed not in (None, "") else None


def code_line(entry: dict | None, key: str) -> str:
    """One code in words: ``"NEC 2023 (statewide, effective 2024-07-01)"`` or ``"NEC: edition not on file"``."""
    c = code(entry, key)
    text = edition(entry, key)
    if text is None:
        return f"{CODE_LABELS.get(key, key.upper())}: edition not on file"
    if key == "ispsc" and c.get("adopted") is False:
        return text
    bits = [b for b in (str(c.get("basis") or ""), f"effective {c['effective']}" if c.get("effective") else "") if b]
    return text + (f" ({', '.join(bits)})" if bits else "")


def _joined(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _ahj_reason(entry: dict, key: str) -> str | None:
    c = code(entry, key)
    if key == "ispsc" and c.get("adopted") is False:
        return None
    basis = str(c.get("basis") or "").lower()
    if basis == "local":
        return "adopted city by city"
    if basis == "none":
        return "no statewide code"
    if c.get("edition") in (None, ""):
        return "no edition on file"
    return None


def ahj_codes(entry: dict | None, keys: tuple = CODES) -> list[str]:
    """The codes a designer must confirm with the authority having jurisdiction: adopted city by city, no
    statewide code, or no edition on file. Every code, with no table row."""
    if not entry:
        return [CODE_LABELS[k] for k in keys]
    return [CODE_LABELS[k] for k in keys if _ahj_reason(entry, k)]


def ahj_note(site: dict | None, entry: dict | None, keys: tuple = CODES) -> str | None:
    """"Confirm the NEC and IBC editions with the authority having jurisdiction: ..." with each code's own
    reason (adopted city by city, no statewide code, no edition on file), or ``None``."""
    labels = ahj_codes(entry, keys)
    if not labels:
        return None
    head = f"Confirm the {_joined(labels)} edition{'s' if len(labels) > 1 else ''} with the authority having jurisdiction"
    if not entry:
        return head + ": there is no jurisdiction data for the site."
    groups: dict[str, list[str]] = {}
    for k in keys:
        reason = _ahj_reason(entry, k)
        if reason:
            groups.setdefault(reason, []).append(CODE_LABELS[k])
    state = state_name(site, entry)
    parts = [f"{_joined(v)}: {r}" for r, v in groups.items()]
    return f"{head}. {state}, " + "; ".join(parts) + "."


def code_report(site: dict | None, key: str) -> dict:
    """What a report says about one code: its ``edition`` line, a ``verify`` sentence, ``warnings`` (the
    AHJ line, and a note when the state enforces another edition than the engine's tables) and ``cite``."""
    entry = entry_for(site)
    label = CODE_LABELS[key]
    state = state_name(site, entry)
    c = code(entry, key)
    engine_ed = ENGINE_EDITIONS.get(key)
    warnings = []
    # With no row the derive raises its own JURISDICTION_NO_DATA issue; the reports only say so in ``verify``.
    note = ahj_note(site, entry, (key,)) if entry else None
    if note:
        warnings.append(note)
    ed = c.get("edition")
    if entry and ed not in (None, "") and engine_ed and str(ed) != str(engine_ed):
        warnings.append(f"{state} enforces {label} {ed}; this engine's rules are written to {label} {engine_ed}. "
                        f"Check each cited section against {label} {ed}.")
    line = code_line(entry, key)
    verify = (f"{state}: {line}. " if entry else f"No jurisdiction data for {state}. ") + (
        note or f"The authority having jurisdiction decides; check local amendments to the {label}.")
    return {"edition": line, "verify": verify, "warnings": warnings, "cite": c.get("cite") or None}


# ------------------------------------------------------------------------- regulation


def spa_regulated(entry: dict | None) -> bool:
    """A spa is regulated unless the table says the state leaves spas alone."""
    return regulation(entry).get("spa_regulated") is not False


def iwf_regulated(entry: dict | None) -> bool:
    return regulation(entry).get("iwf_regulated") is True


def iwf_like_utah(entry: dict | None) -> bool:
    return iwf_regulated(entry) and regulation(entry).get("iwf_like_utah") is True


def fogger_potable(entry: dict | None) -> bool:
    return regulation(entry).get("fogger_potable") is True


def regulated_venues(entry: dict | None) -> list[str]:
    if not entry:
        return []
    out = ["public pools"]
    if spa_regulated(entry):
        out.append("spas")
    if iwf_regulated(entry):
        out.append("interactive water features")
    return out


def licensing_sentence(entry: dict | None) -> str | None:
    lic = _d(_d(entry).get("licensing"))
    parts = [f"Contractor: {lic['contractor']}" if lic.get("contractor") else "",
             f"Electrical: {lic['electrical']}" if lic.get("electrical") else "",
             f"Permits: {lic['permits']}" if lic.get("permits") else ""]
    parts = [p.rstrip(". ") for p in parts if p]
    return ". ".join(parts) + "." if parts else None


# ------------------------------------------------------------------------- climate


def winterize(site: dict | None, entry: dict | None = None) -> bool:
    """Does the site freeze? The site's own ``winterize`` when set, else its frost depth (over zero), else
    the state's flag. With none of them known the answer is yes: a checklist too many is cheap, a burst line
    is not. ``derive.derive_site`` always sets ``winterize``, by the same order for a typed frost depth."""
    s = _d(site)
    if isinstance(s.get("winterize"), bool):
        return s["winterize"]
    frost = _num(s.get("frost_depth_in"))
    if frost is not None:
        return frost > 0
    row = entry if entry is not None else entry_for(site)
    if isinstance(climate(row).get("winterize"), bool):
        return climate(row)["winterize"]
    return True


def dry_climate(site: dict | None, entry: dict | None = None) -> bool | None:
    """True for a dry site, False for a humid one, None when nothing says (see :data:`CIT_DRY`)."""
    s = _d(site)
    row = entry if entry is not None else entry_for(site)
    rh = _num(s.get("rh_pct"))
    if rh is None:
        rh = _num(climate(row).get("design_rh_pct"))
    if rh is not None:
        return rh <= DRY_RH_PCT
    lng = _num(s.get("lng"))
    if lng is not None:
        return lng <= DRY_MERIDIAN
    cl = climate(row)
    elev, air = _num(cl.get("elevation_ft")), _num(cl.get("air_temp_f"))
    if elev is None or air is None:
        return None
    return elev >= DRY_ELEVATION_FT and air >= DRY_AIR_F


# ------------------------------------------------------------------------- the summary


def _cites(entry: dict | None) -> list[str]:
    out: list[str] = []
    for k in (*CODES, "mahc"):
        out.append(code(entry, k).get("cite") or "")
    reg = regulation(entry)
    out += [reg.get(k) or "" for k in ("pool_cite", "iwf_cite", "fogger_cite", "plan_review_cite")]
    out += list(_d(_d(entry).get("licensing")).get("cites") or [])
    seen: list[str] = []
    for c in out:
        if c and isinstance(c, str) and c not in seen:
            seen.append(c)
    return seen


def summary(site: dict | None) -> dict:
    """The ``jurisdiction`` CalcResult the derive returns and the prints read: the code editions, the
    agency, the regulated venues, plan review and licensing, with every cite and the AHJ lines."""
    s = _d(site)
    entry = entry_for(s)
    code_ = code_of(s)
    if not entry:
        return {"calc": "jurisdiction", "value": {"code": code_ or None, "name": None, "known": False, "codes": {},
                                                    "editions": [], "agency": None, "regulated_venues": [],
                                                    "plan_review": None, "licensing": None, "confirm_ahj": True,
                                                    "ahj_note": None, "ahj_local": True, "notes": None},
                "unit": "", "inputs": {}, "formula": "jurisdiction table row", "steps": [no_data_warning(s)],
                "citations": [], "warnings": [no_data_warning(s)], "options": [], "status": "No data"}
    reg = regulation(entry)
    codes = {k: code_line(entry, k) for k in (*CODES, "mahc")}
    editions = [e for e in (edition(entry, k) for k in CODES) if e]
    note = ahj_note(s, entry)
    venues = regulated_venues(entry)
    plan = reg.get("plan_review")
    lic = licensing_sentence(entry)
    steps = [f"{codes[k]}" for k in (*CODES, "mahc")]
    if reg.get("agency"):
        steps.append(f"Health authority: {reg['agency']}")
    steps.append("Regulated: " + (", ".join(venues) if venues else "none listed"))
    if iwf_regulated(entry):
        steps.append(f"Interactive features: {reg.get('iwf_rule') or 'state rule'}"
                     + (" (Utah's R392-302-41 checklist applies as written)" if iwf_like_utah(entry) else
                        " (the state's own rule: the Utah checklist is shown as advice)"))
    if plan:
        steps.append(f"Health-department plan review: {plan}")
    if lic:
        steps.append(lic)
    warnings = [note] if note else []
    return {
        "calc": "jurisdiction",
        "value": {
            "code": code_, "name": entry.get("name"), "known": True, "codes": codes, "editions": editions,
            "agency": reg.get("agency") or None, "regulated_venues": venues, "plan_review": plan or None,
            "pool_rule": reg.get("pool_rule") or None, "iwf_rule": reg.get("iwf_rule") if iwf_regulated(entry) else None,
            "iwf_like_utah": iwf_like_utah(entry), "licensing": lic, "confirm_ahj": bool(note), "ahj_note": note,
            "ahj_local": any(str(code(entry, k).get("basis") or "").lower() in AHJ_BASES for k in CODES),
            "notes": reg.get("notes") or None, "design_city": climate(entry).get("design_city") or None,
        },
        "unit": "",
        "inputs": {"jurisdiction": {"value": code_, "unit": "", "source": "user", "ref": "site.jurisdiction"}},
        "formula": "the state's row of the jurisdiction table (engine/data/jurisdictions.json)",
        "steps": steps,
        "citations": _cites(entry),
        "warnings": warnings,
        "options": [],
        "status": "Confirm with the AHJ" if note else "Okay",
    }
