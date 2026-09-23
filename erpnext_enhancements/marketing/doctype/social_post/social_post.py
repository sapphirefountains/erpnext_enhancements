# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Social Post -- one post, to one or more social accounts (TASK-2026-01481).

One rule lives here, and it is the reason approval means anything: **once a post is approved,
what it says, where it goes and when are locked.** Otherwise the approved text could be edited
after approval and the outbox would publish something nobody approved. The comparison is
``outbox.content_signature``: text, link, publish time, media and each account's variant text
and first comment. Status and results are left out, because the outbox writes those itself
(through ``frappe.db.set_value``, which does not come through here).

Approving, cancelling and re-drafting are TASK-2026-01486's; this only refuses the edit.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.marketing.publish import outbox


def _signature(doc):
	return outbox.content_signature(
		doc,
		[row.as_dict() for row in doc.get("targets") or []],
		[row.as_dict() for row in doc.get("media") or []],
	)


class SocialPost(Document):
	def validate(self):
		self._refuse_duplicate_accounts()
		self._refuse_edits_after_approval()
		self._check_networks()

	def _check_networks(self):
		"""Fill Network Check with what each network would refuse (TASK-2026-01483).

		A draft may be saved with problems -- it is a draft -- but ``outbox.enqueue`` refuses to
		queue a post with any, so they must be fixed before it can go out.
		"""
		from erpnext_enhancements.marketing.publish import validation
		from erpnext_enhancements.marketing.publish.sweeper import account_networks, post_dict, post_parts

		targets, media = post_parts(self)
		networks = account_networks([t["social_account"] for t in targets])
		problems = validation.post_problems(post_dict(self), targets, media, networks)
		self.network_check = "\n".join(problems)

	def _refuse_duplicate_accounts(self):
		seen = set()
		for row in self.get("targets") or []:
			if row.social_account in seen:
				frappe.throw(_("{0} is listed twice under Accounts.").format(row.social_account))
			seen.add(row.social_account)

	def _refuse_edits_after_approval(self):
		before = self.get_doc_before_save()
		if not before or (before.status or outbox.POST_DRAFT) in outbox.EDITABLE_POST_STATUSES:
			return
		if _signature(before) != _signature(self):
			frappe.throw(
				_(
					"This post is {0}: its text, link, media, accounts and publish time are locked to "
					"what was approved. Cancel it and draft a new one to change them."
				).format(before.status),
				title=_("Approved posts cannot be edited"),
			)
