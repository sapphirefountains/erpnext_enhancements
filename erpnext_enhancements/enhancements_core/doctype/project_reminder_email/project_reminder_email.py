# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Project Reminder Email child doctype.

A single recipient ``email`` row (``istable``) in the
``project_reminder_emails`` table of ERPNext Enhancements Settings. The collected
addresses receive the daily project-start reminder
(``project_enhancements.send_project_start_reminders``).

No custom controller logic; behaviour comes from the JSON field definitions.
"""

from frappe.model.document import Document


class ProjectReminderEmail(Document):
	pass
