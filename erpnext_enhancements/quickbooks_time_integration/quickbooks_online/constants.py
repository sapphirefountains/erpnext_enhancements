"""Static configuration for the QuickBooks Online (QBO) accounting integration.

This module is the single source of truth for the QBO REST endpoints, OAuth2
authorization parameters and the entity catalogue used across the
OAuth -> client -> mapping -> sync -> log pipeline. Nothing here performs I/O;
it is pure data imported by ``client.py`` (URLs/OAuth scope), ``sync.py``
(entity ordering and CDC entity list) and ``mapping.py``/``webhooks.py``
(QBO entity -> ERPNext DocType resolution).
"""

# All QBO accounting entities this integration knows how to import. Used as the
# default selection for a full "Import All" run.
ACCOUNTING_ENTITIES = [
	"Account",
	"Customer",
	"Vendor",
	"Item",
	"TaxCode",
	"Invoice",
	"Bill",
	"Payment",
	"JournalEntry",
	"Estimate",
	"PurchaseOrder",
	"Deposit",
]

# Master-data entities are imported first so that transactions (below) can
# resolve their references (e.g. an Invoice's CustomerRef) to already-mapped
# ERPNext records. See sync.ordered_entities for how this ordering is applied.
MASTER_ENTITIES = ["Account", "Customer", "Vendor", "Item", "TaxCode"]
TRANSACTION_ENTITIES = [
	"Estimate",
	"Invoice",
	"Bill",
	"Payment",
	"JournalEntry",
	"PurchaseOrder",
	"Deposit",
]

# Entities polled by the Change Data Capture (CDC) endpoint on the hourly
# scheduler. QBO returns every record of these types changed since a cursor
# timestamp (settings.last_cdc_sync). Note: TaxCode is intentionally omitted
# because QBO's CDC endpoint does not support it.
CDC_ENTITIES = [
	"Account",
	"Customer",
	"Vendor",
	"Item",
	"Invoice",
	"Bill",
	"Payment",
	"JournalEntry",
	"Estimate",
	"PurchaseOrder",
	"Deposit",
]

# API host per environment. The Settings "environment" field (Sandbox/Production)
# selects which base URL the client uses for all data/query/CDC calls.
ENVIRONMENT_BASE_URLS = {
	"Sandbox": "https://sandbox-quickbooks.api.intuit.com",
	"Production": "https://quickbooks.api.intuit.com",
}

# Intuit OAuth2 endpoints (shared across Sandbox and Production).
AUTHORIZATION_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
OAUTH_SCOPE = "com.intuit.quickbooks.accounting"
# Pinned QBO API minor version sent as the "minorversion" query param on every
# data request to lock the response schema this mapping code was written for.
MINOR_VERSION = 75

# Canonical QBO entity name -> native ERPNext DocType. Used to decide whether a
# QBO record has an ERPNext destination at all (webhooks.get_erpnext_doctype)
# and to dispatch to the right mapper (mapping.map_qbo_to_erpnext). Several QBO
# types collapse onto the same ERPNext DocType (e.g. Deposit -> Payment Entry,
# TaxCode -> Account).
ENTITY_DOCTYPE_MAP = {
	"Account": "Account",
	"Customer": "Customer",
	"Vendor": "Supplier",
	"Item": "Item",
	"TaxCode": "Account",
	"Invoice": "Sales Invoice",
	"Bill": "Purchase Invoice",
	"Payment": "Payment Entry",
	"JournalEntry": "Journal Entry",
	"Estimate": "Quotation",
	"PurchaseOrder": "Purchase Order",
	"Deposit": "Payment Entry",
}

