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

ENVIRONMENT_BASE_URLS = {
	"Sandbox": "https://sandbox-quickbooks.api.intuit.com",
	"Production": "https://quickbooks.api.intuit.com",
}

AUTHORIZATION_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
OAUTH_SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = 75

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

