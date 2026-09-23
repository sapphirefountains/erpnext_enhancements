# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Social Publish Attempt -- one entry in a Social Publish Job's attempt log (TASK-2026-01486). Written by the outbox, in the same save as the state change it records."""

from frappe.model.document import Document


class SocialPublishAttempt(Document):
	pass
