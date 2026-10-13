# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The number a call-routing target dials, read from a field this app normalizes on save.

From form kit P2-B (``utils/normalize_hook.py``) a phone with an extension is stored
``+18015550100,123`` — E.164, then the extension after a comma. ``call_routing_match.to_e164``
trusts any value that starts with ``+`` and keeps all of its digits, so it would turn that into
``+18015550100123``: a fifteen-digit number Twilio dials, and a stranger answers. Before P2-B
the same number was stored ``801-555-0100 x123`` and ``to_e164`` refused it (thirteen digits,
no ``+``), so the target was reported as "will not ring". This keeps that answer.

Not a change to ``call_routing_match``: that file is the contract copied byte for byte into
Triton and pinned by shared vectors, so a change there is a two-repo change. This wrapper is
the ERPNext side's guard; Triton's own copy needs the same fix (docs/data-normalization.md).
Stdlib only, like the module it wraps, so the rule and settings controllers can import it
without loading ``call_routing``'s frappe and telephony imports.
"""

from __future__ import annotations

from typing import Any

from erpnext_enhancements.ai_governance import call_routing_match as match


def to_dialable(raw: Any) -> str:
	"""``raw`` as the E.164 string a ``<Number>`` can dial, or ``""``.

	``""`` for a value carrying an extension (a ``,`` or ``;``), which no ``<Number>`` can
	dial as one string; otherwise exactly ``call_routing_match.to_e164``.
	"""
	text = str(raw or "")
	if "," in text or ";" in text:
		return ""
	return match.to_e164(text)
