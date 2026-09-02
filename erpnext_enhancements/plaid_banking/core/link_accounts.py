# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Map native Plaid links onto the company's existing Bank Account masters.

ERPNext's Plaid Link flow (``plaid_settings.add_bank_accounts``) names the Bank
Accounts it creates ``"<Plaid account name> - <bank_name>"``, and Plaid's account
names ("Checking", "Business Savings" ...) are not ours ("Key Bank Checking - Key
Bank", GL 13100). Left alone, linking a bank therefore creates a second Bank
Account per account **and a new GL Account for each**, while the eight masters
that the ledger actually posts to stay unlinked. These helpers exist so that
never has to happen -- or can be undone cleanly when it did.

The Bank record is named by Plaid too. ``add_institution`` looks up ``Bank`` by
Plaid's exact institution string ("KeyBank", "U.S. Bank", "America First Credit
Union") and inserts a NEW Bank with the token when that name does not exist -- so a
plain link puts the token on a Bank our masters do not sit under ("Key Bank"). Two
ways out, both supported here: rename the Bank to Plaid's exact name *before*
linking (``Bank`` has ``allow_rename``; ``add_institution`` then takes its update
branch and the token lands on the existing record), or map afterwards with
``bank=<the Bank that holds the token>``, which moves the master under that Bank.
The token must stay on the Bank Plaid named: the native "Refresh Plaid Link"
re-auth resolves the institution name from Plaid's response and ``get_doc``s that
Bank, so moving the token to a differently named Bank breaks re-authentication.

All helpers are plain functions so they work from ``bench execute``
(``bench execute erpnext_enhancements.plaid_banking.core.link_accounts.map_plaid_account
--kwargs '{"bank_account": "...", "account_id": "..."}'`` -- ``bench execute`` commits
on return), and are whitelisted behind the connect-role gate in ``core/api.py``.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, cint, getdate, today

from erpnext_enhancements.plaid_banking.core.client import PlaidClient
from erpnext_enhancements.plaid_banking.core.utils import error_snippet, linked_banks

NOT_LINKED_HINT = (
	"Plaid names the Bank after the institution, so either rename the Bank to Plaid's exact "
	"institution name before linking, or map with bank=<the Bank that holds the token> to "
	"move this Bank Account under it."
)


def _bank_token(bank: str) -> str:
	"""The access token the native Link stored on this Bank, or throw."""
	if not frappe.db.exists("Bank", bank):
		frappe.throw(f"Bank '{bank}' does not exist.")
	token = _bank_token_or_none(bank)
	if not token:
		frappe.throw(
			f"Bank '{bank}' is not linked to Plaid. Link it first (native Plaid Settings). {NOT_LINKED_HINT}"
		)
	return token


def _bank_token_or_none(bank: str | None) -> str | None:
	return frappe.db.get_value("Bank", bank, "plaid_access_token") if bank else None


def list_plaid_accounts(bank: str) -> list[dict]:
	"""The accounts Plaid holds for this Bank's Item, without balances.

	Returns ``[{account_id, name, official_name, mask, type, subtype}]``.
	"""
	data = PlaidClient().get_accounts(_bank_token(bank))
	return [
		{
			"account_id": a.get("account_id"),
			"name": a.get("name"),
			"official_name": a.get("official_name"),
			"mask": a.get("mask"),
			"type": a.get("type"),
			"subtype": a.get("subtype"),
		}
		for a in (data.get("accounts") or [])
	]


def company_bank_accounts(bank: str | None = None) -> list[dict]:
	"""Our company Bank Accounts (under one Bank, or all), with what they are mapped to."""
	filters = {"is_company_account": 1, "disabled": 0}
	if bank:
		filters["bank"] = bank
	return frappe.get_all(
		"Bank Account",
		filters=filters,
		fields=["name", "bank", "account", "integration_id", "mask", "last_integration_date"],
		order_by="name asc",
	)


def unlinked_company_bank_accounts() -> list[dict]:
	"""Company Bank Accounts whose Bank holds no token -- the masters a plain native link
	strands when Plaid names the Bank differently. Offered as mapping candidates."""
	linked = {row["bank"] for row in linked_banks()}
	return [row for row in company_bank_accounts() if row.get("bank") not in linked]


def mapping_overview() -> dict:
	"""Everything the mapping dialog needs, in one call: per linked bank, the Plaid
	accounts and our Bank Accounts under it; plus, once, the company Bank Accounts under
	Banks that hold no token (candidates to move under a linked Bank). A bank whose
	Plaid call fails is reported with ``error`` rather than aborting the others. Never
	includes a token."""
	out = []
	for row in linked_banks():
		bank = row["bank"]
		entry = {
			"bank": bank,
			"plaid_accounts": [],
			"bank_accounts": company_bank_accounts(bank),
			"error": None,
		}
		try:
			entry["plaid_accounts"] = list_plaid_accounts(bank)
		except Exception as exc:
			entry["error"] = error_snippet(str(exc), 300)
		out.append(entry)
	return {"banks": out, "unlinked_bank_accounts": unlinked_company_bank_accounts() if out else []}


def map_plaid_account(
	bank_account: str, account_id: str, mask: str | None = None, start_date=None, bank: str | None = None
) -> dict:
	"""Stamp a Plaid account id onto an EXISTING company Bank Account.

	Writes ``integration_id`` (+ ``mask`` when given) and ``last_integration_date``
	= ``start_date`` or yesterday, so the native transactions sync's first pull
	starts there instead of dragging in twelve months of history. Validates that
	the Bank Account exists, is a company account, and that no OTHER Bank Account
	already carries this id (the column is unique; a clear message beats a
	UniqueValidationError).

	The token must be on a Bank: the Bank Account's own by default, or ``bank`` when
	native Link named the institution differently ("KeyBank" holds the token, the
	master sits under "Key Bank"). In that case the master's ``bank`` is re-pointed to
	the token-holding Bank as part of the mapping -- refused when the master's own Bank
	already holds a token of its own, because that is a second Plaid Item, not a
	naming mismatch.
	"""
	if not account_id:
		frappe.throw("A Plaid account id is required.")
	row = frappe.db.get_value(
		"Bank Account",
		bank_account,
		["name", "bank", "is_company_account", "integration_id"],
		as_dict=True,
	)
	if not row:
		frappe.throw(f"Bank Account '{bank_account}' does not exist.")
	if not cint(row.get("is_company_account")):
		frappe.throw(f"Bank Account '{bank_account}' is not a company account.")
	repointed_from = _resolve_token_bank(row.get("bank"), bank, bank_account)
	other = frappe.db.get_value(
		"Bank Account", {"integration_id": account_id, "name": ["!=", bank_account]}, "name"
	)
	if other:
		frappe.throw(
			f"Plaid account {account_id} is already mapped to Bank Account '{other}'. "
			"Use absorb_native_duplicate to move it, or clear it there first."
		)

	last_date = getdate(start_date) if start_date else add_days(getdate(today()), -1)
	values = {"integration_id": account_id, "last_integration_date": last_date}
	if mask:
		values["mask"] = mask
	if repointed_from:
		values["bank"] = bank
	frappe.db.set_value("Bank Account", bank_account, values)
	return {
		"bank_account": bank_account,
		"integration_id": account_id,
		"mask": mask,
		"last_integration_date": str(last_date),
		"bank": bank if repointed_from else row.get("bank"),
		"bank_repointed_from": repointed_from,
	}


def _resolve_token_bank(own_bank: str | None, token_bank: str | None, bank_account: str) -> str | None:
	"""Validate where the token is. Returns the master's previous Bank when it must be
	re-pointed to ``token_bank``, else None (the master's own Bank holds the token)."""
	if not token_bank or token_bank == own_bank:
		_bank_token(own_bank)
		return None
	_bank_token(token_bank)
	if _bank_token_or_none(own_bank):
		frappe.throw(
			f"'{bank_account}' sits under Bank '{own_bank}', which holds its own Plaid link; "
			f"it cannot also be moved under '{token_bank}'. Map it to an account of '{own_bank}' instead."
		)
	return own_bank


def absorb_native_duplicate(duplicate: str, into: str) -> dict:
	"""Move a native-Link-created Bank Account's Plaid link onto our master and delete it.

	``duplicate`` is the row ``add_bank_accounts`` auto-created ("<Plaid name> -
	<Bank>"); ``into`` is the master it should have matched. When the two sit under
	different Banks -- Link named the Bank after Plaid's institution ("KeyBank") while
	the master is under ours ("Key Bank") -- the master is re-pointed to the
	duplicate's Bank, the one that holds the token; refused when the master's own
	Bank holds a token too (that is a second Item, and a link cannot move between
	institutions). Refuses when the duplicate already has Bank Transaction rows --
	those reference it by name and re-pointing them is a bank-reconciliation
	decision, not a mapping one.

	``last_integration_date`` is the duplicate's when it has one, else the master's,
	else yesterday: a Link-created row that has never synced carries NULL, and NULL
	means "twelve months, submitted" to the native sync.

	The duplicate's GL Account is deleted too, but ONLY when it is the one the link
	auto-created (``account_name`` == "<Plaid name> - <institution>"), has no GL
	Entry rows, and nothing else links to it (the framework's own link check on
	delete decides that last part). Otherwise it is left in place and the return
	value says why.
	"""
	dup = _bank_account_row(duplicate)
	master = _bank_account_row(into)
	if dup["name"] == master["name"]:
		frappe.throw("duplicate and into are the same Bank Account.")
	if not dup.get("integration_id"):
		frappe.throw(f"'{duplicate}' carries no Plaid link (integration_id is empty); nothing to absorb.")
	repointed_from = None
	if dup["bank"] != master["bank"]:
		if _bank_token_or_none(master["bank"]):
			frappe.throw(
				f"'{duplicate}' belongs to Bank '{dup['bank']}' but '{into}' to '{master['bank']}', "
				"which holds its own Plaid link; a Plaid link cannot move between institutions."
			)
		_bank_token(dup["bank"])
		repointed_from = master["bank"]
	if master.get("integration_id") and master["integration_id"] != dup["integration_id"]:
		frappe.throw(f"'{into}' is already mapped to a different Plaid account ({master['integration_id']}).")
	if frappe.db.count("Bank Transaction", {"bank_account": duplicate}):
		frappe.throw(
			f"'{duplicate}' has Bank Transaction rows. Re-point or cancel them first; "
			"absorbing would orphan them."
		)

	last_date = (
		dup.get("last_integration_date")
		or master.get("last_integration_date")
		or add_days(getdate(today()), -1)
	)
	moved = {
		"integration_id": dup["integration_id"],
		"mask": dup.get("mask"),
		"last_integration_date": last_date,
	}
	if repointed_from:
		moved["bank"] = dup["bank"]
	# Unique column: free the id on the duplicate before stamping the master.
	frappe.db.set_value("Bank Account", duplicate, {"integration_id": None, "mask": None})
	frappe.db.set_value("Bank Account", into, moved)
	frappe.delete_doc("Bank Account", duplicate, ignore_permissions=True)

	result = {
		"absorbed": True,
		"into": into,
		"deleted_duplicate": duplicate,
		"integration_id": moved["integration_id"],
		"last_integration_date": str(last_date),
		"bank": dup["bank"],
		"bank_repointed_from": repointed_from,
		"gl_account": dup.get("account"),
		"gl_account_deleted": False,
		"gl_account_note": None,
	}
	gl_account = dup.get("account")
	if not gl_account:
		result["gl_account_note"] = "duplicate had no GL Account"
		return result
	result["gl_account_deleted"], result["gl_account_note"] = _delete_auto_created_gl_account(
		gl_account, plaid_name=dup.get("account_name"), institution=dup["bank"]
	)
	return result


def prune_link_created_gl_accounts(bank: str) -> dict:
	"""Delete the unused GL Accounts a native re-link left behind for one Bank.

	Once the masters (not the native-named rows) hold the integration ids, every
	"Refresh Plaid Link" re-runs ``add_bank_accounts``: for each shared account it
	inserts a GL Account ``"<Plaid name> - <institution>"`` FIRST, then a Bank Account
	with the same ``integration_id`` -- which fails the unique index and is swallowed
	with a msgprint, so the request commits the GL Account and nothing else. One stray
	Account per shared account per re-authentication. This lists the Item's accounts
	(one ``/accounts/get``), and deletes each Account of that exact name under the same
	guards as the absorb (native pattern, no GL Entry, no Bank Account, framework link
	check inside a savepoint). Returns ``{bank, deleted: [...], kept: [{account, note}]}``.
	"""
	deleted, kept = [], []
	for plaid_account in list_plaid_accounts(bank):
		plaid_name = plaid_account.get("name")
		if not plaid_name:
			continue
		names = frappe.get_all(
			"Account",
			filters={"account_name": f"{plaid_name} - {bank}", "account_type": "Bank", "is_group": 0},
			pluck="name",
		)
		for gl_account in names:
			ok, note = _delete_auto_created_gl_account(gl_account, plaid_name=plaid_name, institution=bank)
			(deleted if ok else kept).append(gl_account if ok else {"account": gl_account, "note": note})
	return {"bank": bank, "deleted": deleted, "kept": kept}


def _bank_account_row(name: str) -> dict:
	row = frappe.db.get_value(
		"Bank Account",
		name,
		["name", "bank", "account", "account_name", "integration_id", "mask", "last_integration_date"],
		as_dict=True,
	)
	if not row:
		frappe.throw(f"Bank Account '{name}' does not exist.")
	return dict(row)


def _delete_auto_created_gl_account(gl_account: str, *, plaid_name: str | None, institution: str):
	"""Delete the GL Account only when it is provably the link's own creation and unused.

	Returns ``(deleted, note)``. The pattern check is the native one: ``add_bank_accounts``
	inserts ``account_name = "<Plaid account name> - <institution name>"`` (the Account's
	``name`` then gets " - <company abbr>" appended by ERPNext). A GL Entry check comes
	first because ``Account.on_trash`` throws on it anyway and its message is worse; the
	remaining "does anything link to it" question is answered by ``delete_doc`` itself,
	inside a savepoint so a refusal leaves the absorb committed and the account untouched.
	A refusal arrives via ``frappe.throw``, which has already queued its message for the
	response; that message is cleared so a whitelisted caller does not see a red error
	dialog over a result that says the absorb succeeded.
	"""
	acct = frappe.db.get_value("Account", gl_account, ["name", "account_name"], as_dict=True)
	if not acct:
		return False, f"GL Account '{gl_account}' no longer exists"
	expected = f"{plaid_name} - {institution}" if plaid_name else None
	if not expected or (acct.get("account_name") or "") != expected:
		return False, (
			f"GL Account '{gl_account}' left alone: its account_name is not the native "
			f"'<Plaid name> - <institution>' pattern ({expected!r}), so it was not auto-created by the link"
		)
	if frappe.db.exists("GL Entry", {"account": gl_account}):
		return False, f"GL Account '{gl_account}' left alone: it has GL Entry rows"
	if frappe.db.exists("Bank Account", {"account": gl_account}):
		return False, f"GL Account '{gl_account}' left alone: another Bank Account still uses it"

	frappe.db.savepoint("plaid_absorb_gl")
	try:
		frappe.delete_doc("Account", gl_account, ignore_permissions=True)
	except Exception as exc:
		frappe.db.rollback(save_point="plaid_absorb_gl")
		frappe.clear_last_message()
		return False, f"GL Account '{gl_account}' left alone: {error_snippet(str(exc), 300)}"
	return True, f"GL Account '{gl_account}' deleted (auto-created by the link, unused)"
