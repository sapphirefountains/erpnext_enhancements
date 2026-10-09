# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One person on a maintenance visit's crew, or on a site's default crew.

Child table of Sapphire Maintenance Record (``crew``: everyone booked on the visit besides the
technician filling it in) and of Sapphire Maintenance Profile (``default_crew``: who goes with the
default technician on every visit to that site, copied onto each drafted visit).

No logic of its own. De-duplication and the hours check run once per save on the parent
(``sapphire_maintenance/visit_crew.py``), and so does the mirror into ordinary assignments
(ToDos), so a crew is never half-validated by whichever row saves first.

``digest_sent_on`` is the per-person twin of the record's ``dispatch_digest_sent_on``: the 6 AM
digest stamps it before texting a helper, so a second run the same day skips them.

The class is named for Frappe's derivation, ``doctype.replace(" ", "")``: ``SapphireVisitCrewMember``.
"""

from frappe.model.document import Document


class SapphireVisitCrewMember(Document):
	pass
