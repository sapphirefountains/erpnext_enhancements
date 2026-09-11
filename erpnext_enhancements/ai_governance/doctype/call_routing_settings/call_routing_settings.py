# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Call Routing Settings — the fallback the rules sit on top of.

Everything a rule does not cover lands here: the number that rings when nothing matches,
how long to ring, the Holiday List rules can defer to, and the voicemail wording.

``validate`` **clamps and never throws.** A Single that has never been saved presents every
field as ``None``/``0``, and a controller that rejects those values is exactly what made
Chat Settings unsaveable in v1.277.3 — a failure that bites hardest on dormant features,
where the very first save is the one you need and the one that fails. Reading with the
fallbacks applied is :func:`ai_governance.call_routing.get_settings`' job, not this class's;
nothing should call ``get_single_value`` on this DocType directly.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from erpnext_enhancements.ai_governance import call_routing_match as match


class CallRoutingSettings(Document):
	def validate(self) -> None:
		"""Clamp into range and normalise the number. Never throws — see the docstring."""
		ring = cint(self.get("default_ring_seconds")) or match.DEFAULT_RING_SECONDS
		self.default_ring_seconds = max(5, min(match.MAX_RING_SECONDS, ring))

		raw = (self.get("default_forward_number") or "").strip()
		if raw:
			normalised = match.to_e164(raw)
			if normalised:
				# Stored in the dialable form so what the form shows is what Twilio is
				# handed. Production stores Employee cell numbers as bare digits, and a
				# 10-digit string on a <Number> is not reliably dialable.
				self.default_forward_number = normalised
			else:
				# Warn rather than throw: an unsaveable settings page is worse than a
				# number that needs fixing, and get_settings drops an unusable value on
				# the way out anyway.
				frappe.msgprint(
					_("{0} is not a phone number Twilio can dial, so no default forward will be used.").format(raw),
					title=_("Check the default forward number"),
					indicator="orange",
				)
