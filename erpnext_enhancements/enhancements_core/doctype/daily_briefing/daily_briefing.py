"""Daily Briefing — the per-user, per-day cached morning briefing.

One row per (user, date), enforced by the ``format:BRIEF-{date}-{user}``
autoname. Rows are written exclusively by ``api.briefing`` (scheduler batch or
the desk force-refresh); direct desk access is System Manager only — users read
their own briefing through the gated ``get_morning_briefing`` endpoint.

A durable DocType (not ``frappe.cache``) on purpose: Redis is flushed by
``bench migrate`` / ``clear-cache`` — exactly when a deploy churns — which
would vaporize every briefing mid-morning and force synchronous Gemini
regenerations. Old rows are purged daily by ``api.briefing.purge_old_briefings``.
"""

from frappe.model.document import Document


class DailyBriefing(Document):
	pass
