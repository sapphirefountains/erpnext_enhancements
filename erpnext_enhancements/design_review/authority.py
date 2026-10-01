"""Who may promote a design decision (ADR 0016 §2).

Promotion files an Enhancement Request that is already ``Approved``, which is a System
Manager's decision. Several accounts hold System Manager without being a person deciding
anything: ``triton@`` is a webhook and sync identity (it holds System Manager for its sync
work), ``mdm@`` is the device-management integration, and ``Administrator`` is the account
the 2026-08-02 compromise came through. None of them may promote, and none may file a
pre-approved request through any other door either, because
``EnhancementRequest.validate`` asks the same question.

The app keeps no registry of service accounts (``crm_enhancements/lead_triage.py`` says so
and lists its own); this is the list for promotion, kept beside the rule that uses it.

Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

SERVICE_ACCOUNTS = frozenset(
	{
		"Administrator",
		"Guest",
		"triton@sapphirefountains.com",
		"mdm@sapphirefountains.com",
	}
)

SYSTEM_MANAGER = "System Manager"


def is_service_account(user: str | None) -> bool:
	return (user or "").strip() in SERVICE_ACCOUNTS or not (user or "").strip()


def is_human_system_manager(user: str | None, roles: list[str] | tuple[str, ...]) -> bool:
	"""A named person holding System Manager. ``roles`` is passed in so this stays pure."""
	return not is_service_account(user) and SYSTEM_MANAGER in (roles or ())
