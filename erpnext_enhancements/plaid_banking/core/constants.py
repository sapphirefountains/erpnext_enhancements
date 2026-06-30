# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Static configuration for the Plaid bank-balance integration.

Mirrors ``quickbooks_online/core/constants.py``: per-environment base URLs,
endpoint paths, and the error-code sets the balance layer branches on. There is
no Plaid SDK — the host can't pip-install — so ``core/client.py`` talks to these
REST endpoints with ``requests`` (a Frappe dependency).
"""

SETTINGS_DOCTYPE = "Plaid Settings"
SNAPSHOT_DOCTYPE = "Bank Balance Snapshot"

# Base URLs per environment (Settings.plaid_environment selects). Plaid retired
# the old "development" tier, so only sandbox + production for real KeyBank data.
ENVIRONMENT_BASE_URLS = {
	"Sandbox": "https://sandbox.plaid.com",
	"Production": "https://production.plaid.com",
}

# Endpoint paths (all POST, application/json; client_id + secret go in the body).
LINK_TOKEN_CREATE = "/link/token/create"
PUBLIC_TOKEN_EXCHANGE = "/item/public_token/exchange"
ACCOUNTS_BALANCE_GET = "/accounts/balance/get"
ITEM_GET = "/item/get"  # Test Connection (cheap, no balances)
ITEM_REMOVE = "/item/remove"  # disconnect: invalidates the access_token at Plaid

# Link-token request parameters. ``/accounts/balance/get`` requires the Item to
# carry an eligible product; "transactions" grants Balance access and is the
# standard product for a durable depository connection (vs the weaker one-off
# "balance" product).
PLAID_PRODUCTS = ["transactions"]
COUNTRY_CODES = ["US"]
LANGUAGE = "en"
CLIENT_NAME = "Sapphire Fountains ERP"

# error_code values that mean "stop retrying until a human reconnects the bank"
# (Item-level). Surfaced to the operator as "Reconnect Required".
NONRETRYABLE_ITEM_ERRORS = {
	"ITEM_LOGIN_REQUIRED",
	"INVALID_ACCESS_TOKEN",
	"INVALID_CREDENTIALS",
	"ITEM_LOCKED",
	"ACCESS_NOT_GRANTED",
	"ITEM_NOT_FOUND",
}
# Config-level (bad API keys). Also pauses, but surfaced as "Error" — the fix is
# correcting the keys, not reconnecting the bank.
NONRETRYABLE_CONFIG_ERRORS = {
	"INVALID_API_KEYS",
	"INVALID_CLIENT_ID",
	"INVALID_SECRET",
	"INVALID_PRODUCT",
	"UNAUTHORIZED_ENVIRONMENT",
}

TIMEOUT = 30
