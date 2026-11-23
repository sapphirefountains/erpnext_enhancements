"""The typed check every phase B calculation reports, for ``issues.py`` (phase E) to map.

A check is a plain dict so it serializes into ``results_json`` as it is::

    {"key": "pump.runout|p1", "severity": "blocker", "title": "...", "remedy": "...",
     "section": "pump", "ref": "p1", "value": {...}}

``severity`` uses the ``issues.py`` vocabulary (``blocker``, ``warning``, ``info``), so phase E
can pass it straight through. ``section`` is the design-model ``setup`` key the finding belongs
to (``basin``, ``piping``, ``pump``, ``control``, ``bypass``, ``surge``, ``treatment``,
``structure``, ``outputs``): that is the section a designer reopens to fix it. ``key`` is stable
across derives (code plus the element id), so an acknowledgement survives a re-derive.
"""

from __future__ import annotations

BLOCKER = "blocker"
WARNING = "warning"
INFO = "info"
SEVERITIES = (BLOCKER, WARNING, INFO)


def make_check(code: str, severity: str, title: str, remedy: str, section: str, *, ref: str = "", value=None) -> dict:
    """One typed check. ``code`` is dotted (``pump.runout``); ``ref`` the node, segment or pump id."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity {severity!r} is not one of {SEVERITIES}")
    return {
        "key": f"{code}|{ref}" if ref else code,
        "code": code,
        "severity": severity,
        "title": title,
        "remedy": remedy,
        "section": section,
        "ref": ref,
        "value": value,
    }


def worst(checks) -> str | None:
    """The most severe severity in a list of checks, or None for an empty list."""
    order = {BLOCKER: 0, WARNING: 1, INFO: 2}
    found = sorted((c["severity"] for c in checks), key=lambda s: order.get(s, 3))
    return found[0] if found else None
