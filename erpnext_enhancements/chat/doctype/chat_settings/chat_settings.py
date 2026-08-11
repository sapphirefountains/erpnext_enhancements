# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for **Chat Settings** — the one kill switch for the whole chat system.

Every Google call, every relay job and every Triton turn reads this Single through
``frappe.get_cached_doc("Chat Settings")`` before doing anything, which is the same shape
``triton_chat.py`` already uses. It ships dormant: ``enabled = 0``, ``dry_run_mode = 1``,
``restrict_to_whitelist = 1``, empty allow-list.

**There is no secret on this DocType and that is the design, not an omission.** ADR §F.13:
under the keyless domain-wide-delegation model there is no private key on disk, no
rotation and no secret in ``site_config.json`` — the entire trust relationship is one IAM
binding on the delegation service account, and revocation is a single
``remove-iam-policy-binding``. Contrast ``Project Folder Google Drive Settings
.service_account_json``, a ``Password`` field holding a 2,358-character RSA private key in
the database of a host on which an attacker has previously executed arbitrary Server
Scripts. If a future change adds a ``Password`` field here for a Google credential, the
design has been misread — :func:`~.chat_settings_rules.detect_secret_material` exists to
make the reflex version of that mistake loud rather than silent.

**The arithmetic lives next door on purpose.** ``chat_settings_rules`` imports nothing at
all, so ``tests/test_chat_settings_budget.py`` can exercise it in the bench-free CI tier.
This module cannot be imported without a Frappe runtime, and this repo has no Frappe
integration-test job — a rule that only ``validate()`` enforces is a rule nothing checks.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, escape_html, flt

from erpnext_enhancements.chat.doctype.chat_settings.chat_settings_rules import (
	detect_secret_material,
	validate_budgets,
	validate_endpoint_url,
	validate_retention,
)

# Fields that hold a Google *identifier* and must never hold a Google *credential*.
# Checked on every save; see the module docstring.
_IDENTIFIER_FIELDS: tuple[str, ...] = (
	"google_project_id",
	"gcp_project_number",
	"workspace_customer_id",
	"workspace_domain",
	"delegation_service_account",
	"vm_service_account_email",
	"chat_app_service_account",
	"interaction_endpoint_url",
	"pubsub_topic",
	"pubsub_events_subscription",
	"pubsub_interaction_subscription",
)

# Trimmed on every save. A pasted identifier picks up a trailing newline far more often
# than anyone expects, and interaction_endpoint_url is compared byte for byte (§G.6).
_TRIMMED_FIELDS: tuple[str, ...] = _IDENTIFIER_FIELDS


class ChatSettings(Document):
	"""Configuration, feature flags, quotas and kill switches for the chat system."""

	def validate(self) -> None:
		"""Check the endpoint URL as typed, then trim, then refuse the expensive mistakes.

		**Order matters here and it is not the obvious one.** ``interaction_endpoint_url``
		is in ``_TRIMMED_FIELDS``, so trimming first would strip the surrounding whitespace
		that :func:`~.chat_settings_rules.validate_endpoint_url` exists to report — leaving
		a rule that appears enforced and is not, on the one field whose byte-exactness the
		whole inbound webhook depends on (ADR §G.6). Silently trimming is the *safer*
		behaviour but the *worse* signal: a pasted value carrying a newline usually means
		the console value carries one too, and the human needs to know that before every
		inbound event 401s. So the endpoint is validated against the raw value first, and
		the reported error order is unchanged.
		"""
		endpoint_errors = validate_endpoint_url(self.get("interaction_endpoint_url") or "")

		self._trim_identifiers()

		errors: list[str] = []
		errors.extend(self._secret_material_errors())
		errors.extend(endpoint_errors)
		errors.extend(self._budget_errors())
		errors.extend(self._retention_errors())
		errors.extend(self._whitelist_errors())

		if errors:
			frappe.throw(
				"<br><br>".join(escape_html(e) for e in errors),
				title=_("Chat Settings"),
			)

	def on_update(self) -> None:
		"""Drop the cached copy so an incident-time switch flip takes effect immediately.

		Every consumer reads this through ``get_cached_doc``. A kill switch that only
		takes effect on the next deploy's Redis FLUSHDB is not a kill switch.
		"""
		frappe.clear_document_cache(self.doctype, self.name)

		# Push the two retention numbers into Frappe's `Logs To Clear`, which is the thing that
		# actually deletes rows. Without this the fields on this form are decorative: they are
		# validated and then read by nothing, and `Chat Relay Job` / `Chat Inbound Event` have
		# no upper bound at all.
		#
		# See `chat/retention.py` for why this is a sync rather than a
		# `default_log_clearing_doctypes` entry. Short version: a static hook and a settings
		# field are two sources of truth for one number, and the hook wins in a way that makes
		# "0 = never" impossible to express — `add_default_logtypes` puts a removed row back.
		#
		# Never raises: an exception here would stop somebody saving a kill switch mid-incident.
		from erpnext_enhancements.chat.retention import sync as sync_log_retention

		sync_log_retention(self)

	# -- validation helpers -------------------------------------------------------

	def _trim_identifiers(self) -> None:
		for fieldname in _TRIMMED_FIELDS:
			# getattr(..., None) or "" rather than self.fieldname: doc_events and
			# validate both fire during ERPNext's own test bootstrap, before this app's
			# fields necessarily exist.
			value = getattr(self, fieldname, None) or ""
			if isinstance(value, str) and value != value.strip():
				setattr(self, fieldname, value.strip())

	def _secret_material_errors(self) -> list[str]:
		errors: list[str] = []
		for fieldname in _IDENTIFIER_FIELDS:
			value = getattr(self, fieldname, None) or ""
			if isinstance(value, str) and detect_secret_material(value):
				label = self.meta.get_label(fieldname) if self.meta else fieldname
				errors.append(
					_(
						"{0} looks like it contains credential material. Chat Settings holds "
						"identifiers only — project ids, service-account email addresses, topic "
						"names and URLs. There is no private key anywhere in this design; "
						"authentication is a single IAM binding on the delegation service account."
					).format(label)
				)
		return errors

	def _budget_errors(self) -> list[str]:
		return validate_budgets(
			cint(self.get("context_token_ceiling")),
			cint(self.get("budget_t0_stable")),
			cint(self.get("budget_t1_thread")),
			cint(self.get("budget_t1_floor")),
			cint(self.get("budget_t2_cross")),
			cint(self.get("budget_t3_authored")),
			cint(self.get("budget_reserve")),
		)

	def _retention_errors(self) -> list[str]:
		return validate_retention(
			cint(self.get("message_retention_days")),
			cint(self.get("hard_delete_after_days")),
			bool(cint(self.get("keep_tombstones_forever"))),
			cint(self.get("inbound_event_retention_days")),
			cint(self.get("relay_job_retention_days")),
		)

	def _whitelist_errors(self) -> list[str]:
		"""One duplicate check, because a duplicated row is a silent no-op otherwise."""
		errors: list[str] = []
		seen: set[str] = set()
		for row in self.get("allowed_users") or []:
			user = (getattr(row, "user", None) or "").strip()
			if not user:
				continue
			if user in seen:
				errors.append(_("{0} is listed more than once in Allowed Users.").format(user))
			seen.add(user)
		return errors


def is_chat_enabled() -> bool:
	"""The master gate, in one place so no caller has to remember two field names.

	Chat is live only when the master switch is on **and** dry-run is off. Reading this
	instead of ``settings.enabled`` is what stops a half-configured site from making a
	real Google call; every path that talks to Google is required to consult it.
	"""
	settings = frappe.get_cached_doc("Chat Settings")
	return bool(cint(settings.get("enabled"))) and not cint(settings.get("dry_run_mode"))


def is_user_allowed(user: str | None = None) -> bool:
	"""Whitelist gate, enforced server-side. Mirrors ``triton_chat.py``'s precedent.

	Administrator is always allowed, because locking the Administrator out of a pilot
	whitelist is how a pilot becomes unrecoverable.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	settings = frappe.get_cached_doc("Chat Settings")
	if not cint(settings.get("restrict_to_whitelist")):
		return True

	return any(
		(getattr(row, "user", None) or "") == user for row in (settings.get("allowed_users") or [])
	)


def http_timeout_seconds() -> float:
	"""The one HTTP timeout for the Google transport, with a sane floor.

	A zero here would mean "no timeout" to ``requests``, which on a hung Google socket
	holds a worker forever. The floor makes the misconfiguration unreachable.
	"""
	settings = frappe.get_cached_doc("Chat Settings")
	return max(flt(settings.get("http_timeout_seconds")) or 30.0, 1.0)
