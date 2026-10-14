"""Lead Source doctype controller: the list a Lead, Opportunity or Customer is attributed to.

Ported from the live site's DB-only custom DocType so that fresh installs can create it
before the Custom Field fixtures that reference it are imported. Records are named by the
source itself ("Cold Call", "Cactus & Tropicals"). It was ported as a child table by mistake;
v1.678.1 made it a standalone list again, because a Link to a child table can be neither
searched nor validated by anyone. No custom controller logic.
"""

from frappe.model.document import Document


class LeadSource(Document):
	pass
