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
	"Estimate",
	"Invoice",
	"SalesReceipt",
	"Bill",
	"VendorCredit",
	"Payment",
	"BillPayment",
	"Purchase",
	"Transfer",
	"CreditCardPayment",
	"JournalEntry",
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
	"SalesReceipt",
	"Bill",
	"VendorCredit",
	"Payment",
	"BillPayment",
	"Purchase",
	"Transfer",
	"CreditCardPayment",
	"JournalEntry",
	"PurchaseOrder",
	"Deposit",
]

# Entities polled by the Change Data Capture (CDC) endpoint on the hourly
# scheduler. QBO returns every record of these types changed since a cursor
# timestamp (settings.last_cdc_sync). Note: TaxCode is intentionally omitted
# because QBO's CDC endpoint does not support it; CreditCardPayment is omitted
# for the same reason (it is a newer entity outside QBO's CDC catalogue), so it
# is incremental-synced only via a periodic full import.
CDC_ENTITIES = [
	"Account",
	"Customer",
	"Vendor",
	"Item",
	"Estimate",
	"Invoice",
	"SalesReceipt",
	"Bill",
	"VendorCredit",
	"Payment",
	"BillPayment",
	"Purchase",
	"Transfer",
	"JournalEntry",
	"PurchaseOrder",
	"Deposit",
]

# QBO rejects a CDC ``changedSince`` cursor older than 30 days. When the stored
# cursor predates that window (e.g. the integration was paused), sync.run_cdc
# clamps it to stay inside the window so the poll still succeeds; anything older
# is reconciled by the next full Import All.
CDC_MAX_LOOKBACK_DAYS = 30

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
	"SalesReceipt": "Sales Invoice",
	"Bill": "Purchase Invoice",
	"Payment": "Payment Entry",
	"JournalEntry": "Journal Entry",
	"Estimate": "Quotation",
	"PurchaseOrder": "Purchase Order",
	# Cash-movement and credit transactions QBO models with explicit GL account
	# lines (rather than items) are imported as balanced Journal Entries.
	"Purchase": "Journal Entry",
	"Transfer": "Journal Entry",
	"BillPayment": "Journal Entry",
	"CreditCardPayment": "Journal Entry",
	"VendorCredit": "Journal Entry",
	"Deposit": "Journal Entry",
}

