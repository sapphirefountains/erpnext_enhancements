# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Static configuration for the Plaid bank-balance widget.

Mirrors ``quickbooks_online/core/constants.py``: per-environment base URLs,
endpoint paths, and the error-code sets the balance layer branches on.
``core/client.py`` talks to these REST endpoints with ``requests`` -- no SDK,
even though ERPNext's own connector uses ``plaid-python``; see the client
docstring for why.
"""

# Our Single: the widget switch, throttle and status. Nothing secret lives here.
SETTINGS_DOCTYPE = "Plaid Banking Settings"
# ERPNext's Single (module ERPNext Integrations): client id, secret, environment,
# the `enabled` / `automatic_sync` switches for the native transactions sync.
# The bank access tokens are NOT on it -- they are per Bank (`Bank.plaid_access_token`).
NATIVE_SETTINGS_DOCTYPE = "Plaid Settings"
SNAPSHOT_DOCTYPE = "Bank Balance Snapshot"

# Base URLs keyed by the NATIVE `Plaid Settings.plaid_env` values (a Select of
# sandbox / development / production). Plaid retired the Development environment
# in 2024; its host no longer answers. A native form still offers the option, so
# it maps to sandbox rather than to a dead hostname -- an operator who picks it
# gets test data and a working widget instead of a connection error that looks
# like bad keys.
ENVIRONMENT_BASE_URLS = {
	"sandbox": "https://sandbox.plaid.com",
	"development": "https://sandbox.plaid.com",
	"production": "https://production.plaid.com",
}
DEFAULT_ENVIRONMENT = "sandbox"

# Endpoint paths (all POST, application/json; client_id + secret go in the body).
ACCOUNTS_BALANCE_GET = "/accounts/balance/get"  # the one call the widget exists for
ACCOUNTS_GET = "/accounts/get"  # account list without balances (mapping helper; cheaper)
ITEM_GET = "/item/get"  # Test Connection (cheap, no balances)

# error_code values that mean "stop retrying THIS bank until a human re-links it"
# (Item-level). Surfaced per bank as "Reconnect Required"; other banks carry on.
NONRETRYABLE_ITEM_ERRORS = {
	"ITEM_LOGIN_REQUIRED",
	"INVALID_ACCESS_TOKEN",
	"INVALID_CREDENTIALS",
	"ITEM_LOCKED",
	"ACCESS_NOT_GRANTED",
	"ITEM_NOT_FOUND",
}
# Config-level (bad API keys). Every bank shares the keys, so this pauses the
# whole widget and is surfaced as "Error" -- the fix is correcting the keys on
# the native form, not re-linking a bank.
NONRETRYABLE_CONFIG_ERRORS = {
	"INVALID_API_KEYS",
	"INVALID_CLIENT_ID",
	"INVALID_SECRET",
	"INVALID_PRODUCT",
	"UNAUTHORIZED_ENVIRONMENT",
}

TIMEOUT = 30
