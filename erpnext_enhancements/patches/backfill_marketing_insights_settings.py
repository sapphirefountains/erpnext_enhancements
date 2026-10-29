"""Give Marketing Settings the v1.757.0 insight and alert figures, and seed the service-line rules.

**The defaults.** The new thresholds (near page one, fading pages, missed-call window, the alert
throttle, drop and review figures), ``alert_roles`` and ``key_pages`` are new fields on a Single, so the
existing row would read them blank and the alerts would sit dormant by accident -- CLAUDE.md's
Single-defaults trap. ``backfill_marketing_settings_defaults`` fills each one **only where tabSingles has
no row for it**, never over a stored value. (``alerts_paused`` defaults to 0, and a missing Check already
reads 0, so the alerts run either way.)

**The service-line rules.** A child table has no default, so an empty mapping would leave the Service
Line Funnel all *Unmatched*. Rows are written straight into ``tabMarketing Service Line Rule`` (not
through a save of the Single, whose ``validate`` this patch has no need to run) **only when the table is
empty**, so rules somebody has edited are never touched. The value-stream rules mirror Sapphire's own
streams; the page and query words are a first guess at the site, to edit in Marketing Settings.

Safe twice: the second run finds rows for everything and writes nothing.
"""

import frappe

from erpnext_enhancements.patches.backfill_marketing_settings_defaults import (
	backfill_marketing_settings_defaults,
)

SETTINGS = "Marketing Settings"
RULE = "Marketing Service Line Rule"

#: In order: the first match wins, so the narrow lines (splash pads, rentals, parts) come before the
#: words that would also match a design-build page or query.
SEED_RULES = (
	("Value Stream", "Design", "Design-Build"),
	("Value Stream", "Build", "Design-Build"),
	("Value Stream", "Service", "Service & Maintenance"),
	("Value Stream", "Events", "Rentals"),
	("Value Stream", "Products", "Parts & Products"),
	("Opportunity Keyword", "splash pad", "Splash Pads"),
	("Opportunity Keyword", "rental", "Rentals"),
	("URL Contains", "splash", "Splash Pads"),
	("URL Contains", "rental", "Rentals"),
	("URL Contains", "event", "Rentals"),
	("URL Contains", "service", "Service & Maintenance"),
	("URL Contains", "maintenance", "Service & Maintenance"),
	("URL Contains", "repair", "Service & Maintenance"),
	("URL Contains", "shop", "Parts & Products"),
	("URL Contains", "product", "Parts & Products"),
	("URL Contains", "parts", "Parts & Products"),
	("URL Contains", "design", "Design-Build"),
	("URL Contains", "portfolio", "Design-Build"),
	("URL Contains", "project", "Design-Build"),
	("Query Keyword", "splash pad", "Splash Pads"),
	("Query Keyword", "rent", "Rentals"),
	("Query Keyword", "repair", "Service & Maintenance"),
	("Query Keyword", "maintenance", "Service & Maintenance"),
	("Query Keyword", "cleaning", "Service & Maintenance"),
	("Query Keyword", "pump", "Parts & Products"),
	("Query Keyword", "nozzle", "Parts & Products"),
	("Query Keyword", "parts", "Parts & Products"),
	("Query Keyword", "fountain", "Design-Build"),
)


def execute() -> None:
	backfill_marketing_settings_defaults()
	seed_service_line_rules()


def seed_service_line_rules() -> int:
	"""Write ``SEED_RULES`` when the table is empty. Returns how many rows it wrote."""
	if not frappe.db.exists("DocType", RULE):
		return 0
	if frappe.db.count(RULE, {"parent": SETTINGS, "parenttype": SETTINGS}):
		return 0
	for idx, (match_type, match_value, line) in enumerate(SEED_RULES, start=1):
		frappe.get_doc(
			{
				"doctype": RULE,
				"parent": SETTINGS,
				"parenttype": SETTINGS,
				"parentfield": "service_line_rules",
				"idx": idx,
				"match_type": match_type,
				"match_value": match_value,
				"service_line": line,
			}
		).db_insert()
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	frappe.db.commit()
	return len(SEED_RULES)
