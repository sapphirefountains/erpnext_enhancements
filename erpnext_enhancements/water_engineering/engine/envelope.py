"""The standard return envelope every engine function produces.

A ``CalcResult`` surfaces the math transparently so the AI (or the desk wizard)
can show its work: the headline ``value``/``unit``, the ``inputs`` it used (each
tagged with where it came from), the ``formula``, the ordered ``steps``, source
``citations``, ``warnings``, any A/B/C ``options`` the user must still pick, and
an optional ``status`` band.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Allowed values for an input's ``source`` tag — lets the AI say exactly where
# every number came from.
INPUT_SOURCES = ("user", "lookup", "prior_calc", "default", "standard")


def make_input(value: Any, unit: str = "", source: str = "user", ref: str = "") -> dict[str, Any]:
    """Build one ``inputs`` entry: the value, its unit, its provenance, and the
    spreadsheet cell / section it traces to."""
    return {"value": value, "unit": unit, "source": source, "ref": ref}


@dataclass
class CalcOption:
    """An A/B/C choice the caller (human or AI) may still need to pick."""

    key: str
    label: str
    value: Any
    recommended: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CalcResult:
    """One calculation's result plus the math that produced it."""

    calc: str
    value: Any = None
    unit: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    formula: str = ""
    steps: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    options: list[CalcOption] = field(default_factory=list)
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict (recurses into ``CalcOption`` rows)."""
        return asdict(self)
