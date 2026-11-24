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

A first native link ALWAYS creates those duplicates (ERPNext 16.60.0): ``add_bank_accounts``
reuses a Bank Account only when one already carries the Plaid ``integration_id`` or the
exact native name, and on a first link none does. So the normal path is "link, then fold
each duplicate into the real account" -- :func:`apply_plaid_mappings` is that step, and
what the **Map Plaid accounts** dialog calls. It also handles credit cards, which have no
master to fold into: ERPNext only accepts a Bank Account whose GL Account is a ledger of
``account_type = "Bank"``, and the QuickBooks sync leaves card accounts untyped on purpose
(``quickbooks_online.core.mapping._account_type``: "Payable" would demand a Party on every
card line; "Bank" demands nothing). :func:`apply_plaid_mapping` with ``card_gl_account``
types that Liability ledger Bank, creates the card's Bank Account (``is_credit_card``), and
folds the Link-created duplicate -- which sits on a new *Asset* GL Account under the Bank
group -- into it.

**The card step is held** (the owner, 2026-11-24: "Banks now, cards later"). Typing a card
ledger Bank makes ERPNext's Cash Flow report count it as cash, so it waits for the CPA
(WI-036, question 8). Every entry point refuses a card row unless the caller passes
``allow_card_ledgers``: :func:`plan_plaid_mapping`, :func:`apply_plaid_mapping` (so
``bench execute`` too) and :func:`apply_plaid_mappings`. The dialog sends it only when its
"Also map credit cards" box is ticked.

All helpers are plain functions so they work from ``bench execute``
(``bench execute erpnext_enhancements.plaid_banking.core.link_accounts.map_plaid_account
--kwargs '{"bank_account": "...", "account_id": "..."}'`` -- ``bench execute`` commits
on return), and are whitelisted behind the connect-role gate in ``core/api.py``.
"""

from __future__ import annotations

import hashlib
import json

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
	includes a token.

	Each Plaid account also says who holds it now: ``holder`` (the Bank Account carrying
	its id, or None) and ``holder_link_created`` (that holder is the duplicate native Link
	made, so the dialog offers to fold it into the real account rather than calling it
	"already mapped"). A ``credit`` account gets ``suggested_card_gl_account`` when exactly
	one Liability ledger's name carries its mask, e.g. "American Express Prime (1008)"."""
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

	holders = _holders([pa.get("account_id") for e in out for pa in e["plaid_accounts"]])
	for entry in out:
		for pa in entry["plaid_accounts"]:
			holder = holders.get(pa.get("account_id"))
			pa["holder"] = holder["name"] if holder else None
			pa["holder_link_created"] = bool(holder) and is_link_created(holder)
			pa["holder_disabled"] = bool(holder) and bool(cint(holder.get("disabled")))
			if pa.get("type") == "credit":
				pa["suggested_card_gl_account"] = _card_gl_suggestion(pa.get("mask"))
	return {"banks": out, "unlinked_bank_accounts": unlinked_company_bank_accounts() if out else []}


def _holders(account_ids) -> dict:
	"""``{integration_id: Bank Account row}`` for the ids some Bank Account carries."""
	ids = [i for i in account_ids if i]
	if not ids:
		return {}
	rows = frappe.get_all(
		"Bank Account",
		filters={"integration_id": ["in", ids]},
		fields=[
			"name",
			"bank",
			"account",
			"account_name",
			"account_type",
			"integration_id",
			"mask",
			"disabled",
		],
	)
	return {row.get("integration_id"): row for row in rows if row.get("integration_id") in ids}


def is_link_created(row) -> bool:
	"""True when this Bank Account is one native Plaid Link created, not a company master.

	The tell is its GL Account. ``add_bank_accounts`` creates the pair together: the Bank
	Account's ``account_name`` is Plaid's account name, and the GL Account it inserts for it
	is named ``"<Plaid account name> - <institution>"``. The institution is the Bank's own
	name, because ``add_institution`` names (or finds) the Bank by it. A master fails the
	test: "Key Bank Checking" sits on a GL Account called "Key Bank Checking". So does a
	master after a re-link has overwritten its ``account_name`` with Plaid's, because its GL
	Account keeps our name. The Bank Account's own name proves nothing either way: a
	master is named ``"<account_name> - <bank>"`` too.
	"""
	gl_account = row.get("account")
	account_name = row.get("account_name")
	if not gl_account or not account_name or not row.get("bank"):
		return False
	gl_name = frappe.db.get_value("Account", gl_account, "account_name")
	return gl_name == f"{account_name} - {row.get('bank')}"


def _card_gl_suggestion(mask) -> str | None:
	"""The one Liability ledger whose name carries ``(<mask>)``, or None when zero or several do.

	Several is real: on prod a card and its central bill account carry the same last four,
	so a guess would be a coin flip and the person picks."""
	if not mask:
		return None
	names = frappe.get_all(
		"Account",
		filters={
			"account_name": ["like", f"%({mask})%"],
			"root_type": "Liability",
			"is_group": 0,
			"disabled": 0,
		},
		pluck="name",
	)
	return names[0] if len(names) == 1 else None


def _feed_start(start_date):
	"""The date the native sync should start from, or None. Refuses a future date.

	``sync_transactions`` asks Plaid for ``last_integration_date`` .. today. A start after
	today is a start after the end, which Plaid rejects, and the native code logs that as a
	"Plaid: Transactions sync error" every hour until the day arrives."""
	if not start_date:
		return None
	start = getdate(start_date)
	if start > getdate(today()):
		frappe.throw(
			f"The feed start date {start} is in the future. The hourly sync asks Plaid for "
			"that date up to today, Plaid refuses a start after the end, and every run would "
			"log an error until then. Pick today or an earlier date."
		)
	return start


def map_plaid_account(
	bank_account: str, account_id: str, mask: str | None = None, start_date=None, bank: str | None = None
) -> dict:
	"""Stamp a Plaid account id onto an EXISTING company Bank Account.

	Writes ``integration_id`` (+ ``mask`` when given) and ``last_integration_date``
	= ``start_date`` or yesterday, so the native transactions sync's first pull
	starts there instead of dragging in twelve months of history. Validates that
	the Bank Account exists, is a company account, carries no OTHER Plaid id (stamping
	over one would silently stop that account's feed), and that no OTHER Bank Account
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
	if row.get("integration_id") and row.get("integration_id") != account_id:
		frappe.throw(
			f"Bank Account '{bank_account}' is already linked to another Plaid account "
			f"({row.get('integration_id')}). Mapping this one onto it would stop that feed. "
			"Clear its Integration ID first if this account really replaces it."
		)
	repointed_from = _resolve_token_bank(row.get("bank"), bank, bank_account)
	other = frappe.db.get_value(
		"Bank Account", {"integration_id": account_id, "name": ["!=", bank_account]}, "name"
	)
	if other:
		frappe.throw(
			f"Plaid account {account_id} is already mapped to Bank Account '{other}'. "
			"Use absorb_native_duplicate to move it, or clear it there first."
		)

	last_date = _feed_start(start_date) or add_days(getdate(today()), -1)
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


def absorb_native_duplicate(duplicate: str, into: str, start_date=None) -> dict:
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

	``last_integration_date`` is ``start_date`` when given (the dialog's "Feed starts on");
	otherwise the duplicate's when it has one, else the master's, else yesterday: a
	Link-created row that has never synced carries NULL, and NULL means "twelve months,
	submitted" to the native sync.

	The duplicate's GL Account is deleted too, but ONLY when it is the one the link
	auto-created (``account_name`` == "<Plaid name> - <institution>"), has no GL
	Entry rows, and nothing else links to it (the framework's own link check on
	delete decides that last part). Otherwise it is left in place and the return
	value says why.
	"""
	start = _feed_start(start_date)
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
		start
		or dup.get("last_integration_date")
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


CARD_TYPE_EFFECT = (
	"ERPNext's Cash Flow report then counts this ledger as cash: its whole history, posted "
	"years included, moves out of operating activities into opening and closing cash. Bank "
	"Reconciliation and Bank Clearance list its past entries as uncleared."
)

#: The plan fields a confirmation is about. Their hash is the fingerprint the dialog sends
#: back, so a row whose plan changed after it was shown is refused instead of run.
_PLAN_FINGERPRINT_FIELDS = (
	"bank",
	"account_id",
	"action",
	"bank_account",
	"create_bank_account",
	"gl_account",
	"set_gl_account_type",
	"repoint_bank_from",
	"duplicate",
	"duplicate_gl_account",
	"duplicate_gl_account_fate",
	"mask",
	"last_integration_date",
)


def plan_plaid_mapping(
	bank: str,
	account_id: str,
	bank_account: str | None = None,
	card_gl_account: str | None = None,
	mask: str | None = None,
	start_date=None,
	disable_duplicate: bool = False,
	plaid_type: str | None = None,
	allow_card_ledgers: bool = False,
) -> dict:
	"""Work out, without writing anything, exactly what mapping one Plaid account will do.

	The dialog's confirmation list is this function's ``changes``, and
	:func:`apply_plaid_mapping` runs the plan this returns, so what a person confirms and
	what the server does come from the same code. Every refusal happens here, before any
	write.

	A row names exactly one of: ``bank_account`` (an existing company master),
	``card_gl_account`` (a credit card's Liability ledger, which gets a Bank Account of its
	own), or ``disable_duplicate`` (no target: switch off the duplicate Plaid Link made for
	an account left unmapped, so the hourly sync skips it). ``bank`` is the Bank holding the
	Plaid token.

	Who carries the Plaid id now decides the action:

	* nobody -> ``mapped``: the id is stamped on the master;
	* the master already -> ``already mapped``: only the mask and start date are refreshed;
	* a row native Link created (:func:`is_link_created`), under this same Bank ->
	  ``absorbed``: the link moves to the master, the duplicate is deleted, and so is its
	  auto-created GL Account when nothing uses it;
	* another master -> refused. Moving a link between two real accounts is a person's call.

	A master that already carries a *different* Plaid id is refused too: stamping over it
	would silently stop that other account's feed.

	A card row is refused unless ``allow_card_ledgers`` (the card hold, see the module
	docstring). A Plaid **credit** account -- known from ``plaid_type`` or from the type native
	Link stamped on its duplicate -- is refused onto a master whose GL Account is not a
	Liability: a card's feed in checking would match nothing and hide the card. And a Plaid
	account known not to be credit is refused onto a card ledger.
	"""
	if not account_id:
		frappe.throw("A Plaid account id is required.")
	targets = [bool(bank_account), bool(card_gl_account), bool(disable_duplicate)]
	if targets.count(True) != 1:
		frappe.throw(
			"Pick a company Bank Account, a card's GL account, or switching off the duplicate "
			"for this Plaid account: exactly one."
		)
	if card_gl_account and not allow_card_ledgers:
		frappe.throw(CARDS_HELD_MESSAGE)
	_bank_token(bank)
	start = _feed_start(start_date)
	holder = _holders([account_id]).get(account_id)
	holder_is_duplicate = bool(holder) and is_link_created(holder)
	if holder_is_duplicate and holder.get("bank") != bank:
		frappe.throw(
			f"Plaid account {account_id} sits on '{holder['name']}' under Bank '{holder.get('bank')}', "
			f"not under '{bank}'. Reopen Map Plaid accounts."
		)
	known_type = (plaid_type or (holder.get("account_type") if holder_is_duplicate else None) or "").lower()
	if card_gl_account and known_type and known_type != "credit":
		frappe.throw(
			f"Plaid lists account {account_id} as {known_type}, not a credit card. Map it to its "
			"company Bank Account instead of a card's GL account."
		)

	plan = {key: None for key in _PLAN_FINGERPRINT_FIELDS}
	plan.update(
		{
			"bank": bank,
			"account_id": account_id,
			"create_bank_account": False,
			"set_gl_account_type": False,
			"changes": [],
			"duplicate_gl_account_note": None,
		}
	)
	changes = plan["changes"]

	if disable_duplicate:
		if not holder_is_duplicate:
			frappe.throw(
				f"Nothing to switch off for Plaid account {account_id}: no row Plaid Link created holds it."
			)
		plan.update({"action": "disable duplicate", "duplicate": holder["name"]})
		changes.append(
			f"'{holder['name']}', which Plaid Link created, is switched off (Disabled): it keeps "
			"its Plaid link, but the hourly sync skips it until it is mapped."
		)
		return _with_fingerprint(plan)

	if card_gl_account:
		card = _card_plan(card_gl_account, bank)
		target, target_exists = card["bank_account"], not card["create"]
		plan.update(
			{
				"gl_account": card_gl_account,
				"set_gl_account_type": card["set_type"],
				"create_bank_account": card["create"],
			}
		)
		if card["set_type"]:
			changes.append(
				f"GL account '{card_gl_account}': Account Type changes from blank to Bank. {CARD_TYPE_EFFECT}"
			)
		if card["create"]:
			changes.append(
				f"Bank Account '{target}' is created on '{card_gl_account}' under Bank '{bank}', "
				"with Is Credit Card ticked."
			)
	else:
		target, target_exists = bank_account, True
	plan["bank_account"] = target

	if holder and not holder_is_duplicate and holder["name"] != (target if target_exists else None):
		frappe.throw(
			f"Plaid account {account_id} is already on '{holder['name']}', a company account "
			"rather than a row Plaid Link created. Clear its Integration ID there first if it "
			"really belongs on another account."
		)

	trow = None
	if target_exists:
		trow = frappe.db.get_value(
			"Bank Account",
			target,
			[
				"name",
				"bank",
				"account",
				"is_company_account",
				"disabled",
				"integration_id",
				"mask",
				"last_integration_date",
			],
			as_dict=True,
		)
		if not trow:
			frappe.throw(f"Bank Account '{target}' does not exist.")
		if not cint(trow.get("is_company_account")):
			frappe.throw(f"Bank Account '{target}' is not a company account.")
		if cint(trow.get("disabled")):
			frappe.throw(f"Bank Account '{target}' is disabled; the sync skips disabled accounts.")
		if trow.get("integration_id") and trow.get("integration_id") != account_id:
			frappe.throw(
				f"Bank Account '{target}' is already linked to another Plaid account "
				f"({trow.get('integration_id')}). Mapping this one onto it would stop that feed. "
				"Clear its Integration ID first if this account really replaces it."
			)
		if bank_account and known_type == "credit":
			root_type = frappe.db.get_value("Account", trow.get("account"), "root_type")
			if root_type != "Liability":
				frappe.throw(
					f"Plaid lists account {account_id} as a credit card, but '{target}' sits on GL "
					f"account '{trow.get('account')}', an {root_type or 'untyped'} account, not a Liability. A "
					"card's feed belongs on the card's own ledger (held until the CPA answers)."
				)

	if holder is None:
		plan["action"] = "mapped"
		if trow and trow.get("bank") != bank:
			_resolve_token_bank(trow.get("bank"), bank, target)
			plan["repoint_bank_from"] = trow.get("bank")
		plan["mask"] = mask or None
		plan["last_integration_date"] = str(start or add_days(getdate(today()), -1))
	elif target_exists and holder["name"] == target:
		plan["action"] = "already mapped"
		if mask and mask != holder.get("mask"):
			plan["mask"] = mask
		if start:
			plan["last_integration_date"] = str(start)
	else:
		plan["action"] = "absorbed"
		dup = _bank_account_row(holder["name"])
		if trow and trow.get("bank") != bank:
			if _bank_token_or_none(trow.get("bank")):
				frappe.throw(
					f"'{target}' sits under Bank '{trow.get('bank')}', which holds its own Plaid link; "
					"a Plaid link cannot move between institutions."
				)
			plan["repoint_bank_from"] = trow.get("bank")
		if frappe.db.count("Bank Transaction", {"bank_account": dup["name"]}):
			frappe.throw(
				f"'{dup['name']}' already has Bank Transaction rows, so it cannot be folded in. "
				"Re-point or cancel them first."
			)
		fate, note = _gl_account_fate(
			dup.get("account"),
			plaid_name=dup.get("account_name"),
			institution=dup.get("bank"),
			ignore_bank_account=dup["name"],
		)
		plan.update(
			{
				"duplicate": dup["name"],
				"duplicate_gl_account": dup.get("account"),
				"duplicate_gl_account_fate": fate,
				"duplicate_gl_account_note": note,
				"mask": dup.get("mask") or None,
				"last_integration_date": str(
					start
					or dup.get("last_integration_date")
					or (trow or {}).get("last_integration_date")
					or add_days(getdate(today()), -1)
				),
			}
		)

	if plan["action"] in ("mapped", "absorbed"):
		changes.append(f"Bank Account '{target}' gets Plaid account {account_id}.")
	if plan["repoint_bank_from"]:
		changes.append(f"Bank Account '{target}' moves from Bank '{plan['repoint_bank_from']}' to '{bank}'.")
	if plan["duplicate"]:
		gl_line = (
			f"its GL account '{plan['duplicate_gl_account']}' is deleted too, unless something else links to it"
			if plan["duplicate_gl_account_fate"] == "delete"
			else f"its GL account is kept ({plan['duplicate_gl_account_note']})"
		)
		changes.append(f"'{plan['duplicate']}', which Plaid Link created, is deleted; {gl_line}.")
	if plan["mask"]:
		changes.append(f"Mask set to {plan['mask']}.")
	if plan["last_integration_date"]:
		changes.append(
			f"Last Integration Date set to {plan['last_integration_date']}: the feed starts there."
		)
	if not changes:
		changes.append("Nothing changes.")
	return _with_fingerprint(plan)


def _with_fingerprint(plan: dict) -> dict:
	core = {key: plan.get(key) for key in _PLAN_FINGERPRINT_FIELDS}
	plan["fingerprint"] = hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()
	return plan


def _card_plan(gl_account: str, bank: str) -> dict:
	"""Validate a credit card's GL Account and say what the card step would do to it.

	Returns ``{bank_account, create, set_type}``. The ledger must be an enabled Liability
	account: a card is money owed, and this refuses an Asset account rather than quietly
	turning a checking ledger into a card. ERPNext lets one GL Account back only one Bank
	Account, so a company Bank Account already on it is the card's master and is reused
	(a re-run is harmless); one that is disabled, not a company account, or another
	company's is refused rather than adopted. Otherwise the type must be blank (how the
	QuickBooks sync leaves cards) or Bank, and the new Bank Account's name is ERPNext's
	autoname for it, ``"<account_name> - <bank>"``.
	"""
	acc = frappe.db.get_value(
		"Account",
		gl_account,
		["name", "account_name", "company", "root_type", "account_type", "is_group", "disabled"],
		as_dict=True,
	)
	if not acc:
		frappe.throw(f"GL Account '{gl_account}' does not exist.")
	if cint(acc.get("is_group")) or cint(acc.get("disabled")):
		frappe.throw(
			f"GL Account '{gl_account}' must be an enabled ledger, not a group or a disabled account."
		)
	if acc.get("root_type") != "Liability":
		frappe.throw(
			f"A card's GL account must be a Liability account; '{gl_account}' is "
			f"{acc.get('root_type') or 'not typed'}."
		)
	existing = frappe.db.get_value(
		"Bank Account",
		{"account": gl_account},
		["name", "is_company_account", "disabled", "company"],
		as_dict=True,
	)
	if existing:
		if (
			not cint(existing.get("is_company_account"))
			or cint(existing.get("disabled"))
			or existing.get("company") != acc.get("company")
		):
			frappe.throw(
				f"Bank Account '{existing.get('name')}' already uses '{gl_account}' but is disabled, "
				"not a company account, or another company's. Fix or remove it first."
			)
		return {"bank_account": existing.get("name"), "create": False, "set_type": False}
	account_type = acc.get("account_type") or ""
	if account_type not in ("", "Bank"):
		frappe.throw(
			f"GL Account '{gl_account}' has Account Type '{account_type}'. A card's GL account "
			"must be untyped (as the QuickBooks sync leaves cards) or Bank."
		)
	name = f"{acc.get('account_name')} - {bank}"
	if frappe.db.exists("Bank Account", name):
		frappe.throw(f"A Bank Account named '{name}' already exists on another GL account.")
	return {"bank_account": name, "create": True, "set_type": not account_type}


def apply_plaid_mapping(
	bank: str,
	account_id: str,
	bank_account: str | None = None,
	card_gl_account: str | None = None,
	mask: str | None = None,
	start_date=None,
	disable_duplicate: bool = False,
	expected_fingerprint: str | None = None,
	plaid_type: str | None = None,
	allow_card_ledgers: bool = False,
) -> dict:
	"""Run :func:`plan_plaid_mapping`'s plan for one Plaid account. The dialog's per-row write.

	``expected_fingerprint`` is the plan the person confirmed. When it no longer matches --
	a duplicate appeared, a master was mapped in another tab -- the row is refused rather
	than doing something that was never shown. The steps are the existing helpers, which
	re-check their own preconditions: the card's ledger is typed, its Bank Account created,
	then :func:`map_plaid_account` or :func:`absorb_native_duplicate` moves the link.
	"""
	plan = plan_plaid_mapping(
		bank,
		account_id,
		bank_account=bank_account,
		card_gl_account=card_gl_account,
		mask=mask,
		start_date=start_date,
		disable_duplicate=disable_duplicate,
		plaid_type=plaid_type,
		allow_card_ledgers=allow_card_ledgers,
	)
	if expected_fingerprint and plan["fingerprint"] != expected_fingerprint:
		frappe.throw(
			f"Plaid account {account_id} changed after the list you confirmed was made. Nothing was "
			"done for it; reopen Map Plaid accounts to see what it would do now."
		)
	start = _feed_start(start_date)
	result = {key: plan.get(key) for key in (*_PLAN_FINGERPRINT_FIELDS, "duplicate_gl_account_note")}
	result.update({"card_master_created": False, "gl_account_type_set": False, "gl_account_note": None})

	if plan["action"] == "disable duplicate":
		frappe.db.set_value("Bank Account", plan["duplicate"], "disabled", 1)
		return result

	target = plan["bank_account"]
	if plan["gl_account"]:
		if plan["set_gl_account_type"]:
			account = frappe.get_doc("Account", plan["gl_account"])
			account.account_type = "Bank"
			account.save(ignore_permissions=True)
			result["gl_account_type_set"] = True
		if plan["create_bank_account"]:
			target = _create_card_master(plan["gl_account"], bank, plan["duplicate"])
			result["card_master_created"] = True
			result["bank_account"] = target

	if plan["action"] == "mapped":
		map_plaid_account(target, account_id, mask=mask, start_date=start, bank=bank)
	elif plan["action"] == "already mapped":
		values = {}
		if plan["last_integration_date"]:
			values["last_integration_date"] = start
		if plan["mask"]:
			values["mask"] = plan["mask"]
		if values:
			frappe.db.set_value("Bank Account", target, values)
	else:
		absorbed = absorb_native_duplicate(plan["duplicate"], target, start_date=start)
		result["deleted_duplicate"] = absorbed["deleted_duplicate"]
		result["gl_account_note"] = absorbed["gl_account_note"]
	result["last_integration_date"] = str(
		frappe.db.get_value("Bank Account", target, "last_integration_date") or ""
	)
	return result


def _create_card_master(gl_account: str, bank: str, duplicate: str | None) -> str:
	"""Insert the card's Bank Account on its (now Bank-typed) ledger; returns its name.

	Under the token-holding Bank, named after the ledger, ``is_credit_card`` ticked
	(16.60+: vouchers made from its Bank Transactions become "Credit Card Entry"), and
	Plaid's account type and subtype copied from the Link-created duplicate when there is one.
	"""
	acc = frappe.db.get_value("Account", gl_account, ["account_name", "company"], as_dict=True)
	values = {
		"doctype": "Bank Account",
		"account_name": acc.get("account_name"),
		"bank": bank,
		"account": gl_account,
		"is_company_account": 1,
		"company": acc.get("company"),
	}
	if duplicate:
		dup = frappe.db.get_value(
			"Bank Account", duplicate, ["account_type", "account_subtype"], as_dict=True
		)
		for fieldname in ("account_type", "account_subtype"):
			if dup and dup.get(fieldname):
				values[fieldname] = dup.get(fieldname)
	if _has_field("Bank Account", "is_credit_card"):
		values["is_credit_card"] = 1
	master = frappe.get_doc(values)
	master.insert(ignore_permissions=True)
	return master.name


def _has_field(doctype: str, fieldname: str) -> bool:
	"""``is_credit_card`` arrived in ERPNext 16.60; the insert must not name a column an
	older site lacks."""
	try:
		return bool(frappe.get_meta(doctype).has_field(fieldname))
	except Exception:
		return False


CARDS_HELD_MESSAGE = (
	"Card mapping is off. Mapping a card types its GL account Bank, and ERPNext's Cash Flow "
	"report then counts the card as cash. The cards are held until the CPA confirms how card "
	"balances should appear (WI-036, question 8). Once that is settled, tick the dialog's card "
	"option to map them."
)


def apply_plaid_mappings(rows, start_date=None, dry_run=False, allow_card_ledgers=False) -> dict:
	"""The dialog's two calls: plan every row (``dry_run``), then run the confirmed plans.

	``rows`` is a list of ``{bank, account_id, mask, bank_account | card_gl_account |
	disable_duplicate, fingerprint}``. The start date is checked once, before anything.

	A ``card_gl_account`` row is refused unless ``allow_card_ledgers`` is set: the card step
	changes a GL Account's type and with it the Cash Flow statement, and the owner held it
	(2026-11-24, "Banks now, cards later") until the CPA answers. The dialog sets the flag only
	when its card option is ticked, so nothing in the default flow types a ledger Bank.

	* ``dry_run``: :func:`plan_plaid_mapping` per row, messages muted, nothing written.
	  Returns ``{start_date, plans: [...]}``; a refused row is ``{ok: False, message}``.
	* otherwise: :func:`apply_plaid_mapping` per row with the row's ``fingerprint``, each in
	  its own savepoint with messages muted, so one refusal is rolled back and reported while
	  the rows around it commit. Returns ``{start_date, results: [...]}``.

	If the database rolled back the whole transaction (a deadlock does; the savepoint is
	then gone too), the batch stops and says nothing was saved, rather than reporting the
	earlier rows as done.
	"""
	start = _feed_start(start_date)
	flags = frappe.flags
	out = []
	for row in rows or []:
		row = row or {}
		if row.get("card_gl_account") and not allow_card_ledgers:
			out.append(
				{
					"ok": False,
					"bank": row.get("bank"),
					"account_id": row.get("account_id"),
					"message": CARDS_HELD_MESSAGE,
				}
			)
			continue
		kwargs = {
			"bank_account": row.get("bank_account") or None,
			"card_gl_account": row.get("card_gl_account") or None,
			"mask": row.get("mask") or None,
			"start_date": start,
			"disable_duplicate": bool(row.get("disable_duplicate")),
			"plaid_type": row.get("plaid_type") or None,
			"allow_card_ledgers": bool(allow_card_ledgers),
		}
		previous = getattr(flags, "mute_messages", False)
		flags.mute_messages = True
		if dry_run:
			try:
				out.append(
					{"ok": True, **plan_plaid_mapping(row.get("bank"), row.get("account_id"), **kwargs)}
				)
			except Exception as exc:
				out.append(_refused(row, exc))
			finally:
				flags.mute_messages = previous
			continue

		frappe.db.savepoint("plaid_map_row")
		try:
			result = apply_plaid_mapping(
				row.get("bank"),
				row.get("account_id"),
				expected_fingerprint=row.get("fingerprint") or None,
				**kwargs,
			)
			out.append({"ok": True, **result})
		except Exception as exc:
			if row.get("card_gl_account"):
				# The Account save cleared its cache entry, and Bank Account.validate may have
				# cached it again as Bank before the rollback reverts the row.
				frappe.clear_document_cache("Account", row.get("card_gl_account"))
			try:
				frappe.db.rollback(save_point="plaid_map_row")
			except Exception:
				flags.mute_messages = previous
				frappe.throw(
					"The database rolled back the whole batch, so nothing was saved, not even the "
					f"accounts before this one ({error_snippet(str(exc), 200)}). Open Map Plaid "
					"accounts and run it again."
				)
			out.append(_refused(row, exc))
		finally:
			flags.mute_messages = previous
	key = "plans" if dry_run else "results"
	return {"start_date": str(start) if start else None, key: out}


def _refused(row: dict, exc: Exception) -> dict:
	return {
		"ok": False,
		"bank": row.get("bank"),
		"account_id": row.get("account_id"),
		"message": error_snippet(str(exc), 300),
	}


def prune_link_created_gl_accounts(bank: str) -> dict:
	"""Delete the unused GL Accounts a native re-link left behind for one Bank.

	Written against ERPNext 16.50: once the masters (not the native-named rows) held the
	integration ids, every "Refresh Plaid Link" re-ran ``add_bank_accounts``, which
	inserted a GL Account ``"<Plaid name> - <institution>"`` FIRST and then failed the
	Bank Account insert on the unique ``integration_id`` -- one stray Account per shared
	account per re-authentication. ERPNext 16.60.0 looks the Bank Account up by
	``integration_id`` first and updates it in place, so a re-link there creates no stray
	Account (it overwrites the master's ``account_name`` with Plaid's instead -- cosmetic,
	the name and GL Account stay). Kept because it is harmless when there is nothing to
	prune, and an account Plaid adds to the Item later still takes the create path. This
	lists the Item's accounts
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


def _gl_account_fate(
	gl_account: str | None, *, plaid_name: str | None, institution: str | None, ignore_bank_account=None
) -> tuple[str, str]:
	"""Whether a Link-created duplicate's GL Account would be deleted: ``("delete" | "keep", note)``.

	The same guards :func:`_delete_auto_created_gl_account` applies, read-only, so a plan can
	show the outcome. ``ignore_bank_account`` is the duplicate itself, which still uses the
	account at plan time. "delete" still means "unless something else links to it": that
	last check is ``delete_doc``'s own and runs only at delete time.
	"""
	if not gl_account:
		return "keep", "the duplicate has no GL Account"
	acct = frappe.db.get_value("Account", gl_account, ["name", "account_name"], as_dict=True)
	if not acct:
		return "keep", f"GL Account '{gl_account}' no longer exists"
	expected = f"{plaid_name} - {institution}" if plaid_name else None
	if not expected or (acct.get("account_name") or "") != expected:
		return "keep", (
			f"GL Account '{gl_account}' left alone: its account_name is not the native "
			f"'<Plaid name> - <institution>' pattern ({expected!r}), so it was not auto-created by the link"
		)
	if frappe.db.exists("GL Entry", {"account": gl_account}):
		return "keep", f"GL Account '{gl_account}' left alone: it has GL Entry rows"
	users = {"account": gl_account}
	if ignore_bank_account:
		users["name"] = ["!=", ignore_bank_account]
	if frappe.db.exists("Bank Account", users):
		return "keep", f"GL Account '{gl_account}' left alone: another Bank Account still uses it"
	return "delete", f"GL Account '{gl_account}' is unused and was auto-created by the link"


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
	fate, note = _gl_account_fate(gl_account, plaid_name=plaid_name, institution=institution)
	if fate != "delete":
		return False, note

	frappe.db.savepoint("plaid_absorb_gl")
	try:
		frappe.delete_doc("Account", gl_account, ignore_permissions=True)
	except Exception as exc:
		try:
			frappe.db.rollback(save_point="plaid_absorb_gl")
		except Exception:
			# The savepoint is gone because the database already rolled back the whole
			# transaction (a deadlock does that). Nothing here is still true; let the caller see it.
			raise exc from None
		# Under mute_messages (the batch) the throw queued nothing, so clearing would drop
		# somebody else's message instead.
		if not getattr(getattr(frappe, "flags", None), "mute_messages", False):
			frappe.clear_last_message()
		return False, f"GL Account '{gl_account}' left alone: {error_snippet(str(exc), 300)}"
	return True, f"GL Account '{gl_account}' deleted (auto-created by the link, unused)"
