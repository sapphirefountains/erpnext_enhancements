"""The electrical one-line from this package's results: service, feeder, panel,
branch circuits with breaker sizes and conductors, drives and transformers. It
builds the state :func:`engine.drawings.electrical_oneline_svg` draws, so the
packet and the designer page share one renderer.
"""

from __future__ import annotations

from ..drawings import electrical_oneline_svg
from ._util import num


def _wire_note(c: dict) -> str:
    if not c.get("conductor"):
        return ""
    note = f"#{c['conductor']}" + (f" + #{c['egc']}G" if c.get("egc") else "")
    if c.get("conduit"):
        note += f", {c['conduit']} {c.get('conduit_type') or ''}".rstrip()
    if c.get("drop_pct") is not None:
        note += f", {num(c['drop_pct']):.1f}% VD"
    return note


def oneline_state(
    circuits: list[dict] | None,
    *,
    service: dict | None = None,
    feeder: dict | None = None,
    transformers: list[dict] | None = None,
    title: str = "Electrical One-Line",
) -> dict:
    """State for ``electrical_oneline_svg``. ``service`` = ``{voltage, phase,
    main_breaker, available_a}``; ``feeder`` = ``{conductor, egc, conduit,
    length_ft, drop_pct, ocpd}``; ``transformers`` = ``[{label, sub}]``."""
    service = service or {}
    svc_label = f"{num(service.get('voltage'), 240):g} V {int(num(service.get('phase'), 1))}φ Service"
    sub = f"{num(service['available_a']):g} A available" if service.get("available_a") else ""
    state: dict = {
        "title": title,
        "service": {"label": svc_label, "main_breaker": service.get("main_breaker"), "sub": sub},
        "panel": {"label": "Fountain panel"},
        "branches": [],
        "transformers": list(transformers or []),
    }
    if feeder and num(feeder.get("length_ft")) > 0:
        state["feeder"] = {
            "label": f"Feeder {_wire_note({**feeder, 'drop_pct': None})}",
            "sub": f"{num(feeder.get('length_ft')):g} ft, {num(feeder.get('drop_pct')):.1f}% VD",
        }
    for c in circuits or []:
        bk = f"{c.get('breaker_a')} A {c.get('poles')}P" + (" GFCI" if c.get("gfci") else "")
        load = f"{num(c.get('load_a')):.1f} A, {num(c.get('voltage')):g} V {c.get('phase')}φ"
        if c.get("hp"):
            load = f"{num(c['hp']):g} HP, " + load
        state["branches"].append({
            "label": f"{c.get('tag')} {c.get('description') or ''}".strip(),
            "sub": load,
            "breaker": bk,
            "wire": _wire_note(c),
            "vfd": f"VFD {num(c['vfd_hp']):g} HP" if c.get("vfd_hp") else None,
        })
    return state


def electrical_oneline(circuits: list[dict] | None, **kw) -> str:
    """The one-line SVG for ``circuits`` (see :func:`oneline_state` for ``kw``)."""
    return electrical_oneline_svg(oneline_state(circuits, **kw))


__all__ = ["electrical_oneline", "oneline_state"]
