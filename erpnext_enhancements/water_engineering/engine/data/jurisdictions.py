"""The per-state jurisdiction table (Fountain Designer phase J).

``jurisdictions.json`` beside this file holds one row per state, keyed by its USPS code: the code
editions it enforces (NEC, IBC, IRC, ISPSC, MAHC), who regulates pools, spas and interactive
features and how, the design city's climate, and the licensing and permit notes. Its schema is
fixed and shared with the research that fills it (``docs/fountain-designer/PLAN.md``, "Phase J:
jurisdictions"); ``docs/fountain-designer/catalog/jurisdictions.json`` is the researched source
and ``tests/test_jurisdictions.py`` holds the two equal. Any value may be null: an uncited value
is left out rather than guessed.

The file ships inside the package (Frappe apps install editable, and flit packs every file under
the module), so it is read from disk next to this module, once, and cached.

An unknown or blank code returns ``None``. A caller treats that as "no jurisdiction data": it
warns and asks the designer for the site facts. It never falls back to Utah.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("jurisdictions.json")
SCHEMA = 1
UTAH = "UT"

_TABLE: dict[str, dict[str, Any]] | None = None


def _load() -> dict[str, dict[str, Any]]:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    rows = data.get("jurisdictions") if isinstance(data, dict) else None
    return {str(k).upper(): v for k, v in (rows or {}).items() if isinstance(v, dict)}


def table() -> dict[str, dict[str, Any]]:
    """Every row by code, read once. Tests add a fixture state with ``monkeypatch.setitem``."""
    global _TABLE
    if _TABLE is None:
        _TABLE = _load()
    return _TABLE


def get(code: Any) -> dict[str, Any] | None:
    """The row for a USPS code (any case), or ``None`` for a blank or unknown code."""
    key = str(code or "").strip().upper()
    return table().get(key) if key else None


def codes() -> list[str]:
    """Every code in the table, Utah first and then by state name (the picker's order)."""
    rows = table()
    return sorted(rows, key=lambda k: (k != UTAH, str(rows[k].get("name") or k)))


def names() -> dict[str, str]:
    """``{code: state name}`` in :func:`codes` order."""
    rows = table()
    return {k: str(rows[k].get("name") or k) for k in codes()}


def name(code: Any) -> str:
    """The state's name, or the code itself (or "the site's state") when the table has no row."""
    row = get(code)
    if row and row.get("name"):
        return str(row["name"])
    return str(code or "").strip().upper() or "the site's state"
