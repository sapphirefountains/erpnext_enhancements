# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One person on a session roster.

No logic; the parent resolves the user and stamps the completion. The file still
has to exist — `bench migrate` calls `load_doctype_module` for every DocType
including child tables, and a missing controller aborts the migrate partway and
fails every subsequent deploy the same way (v1.268.0 took the pipeline down
exactly like this).

`attended` defaults on and can be unticked. Somebody who was on the list and did
not turn up must not end up certified: a roster that quietly records absentees as
trained is worse than no roster at all.
"""

from frappe.model.document import Document


class TrainingSessionAttendee(Document):
	pass
