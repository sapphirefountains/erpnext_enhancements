# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Draft -> approve -> publish: the rules, and who may do each (TASK-2026-01486).

Decision 9: nothing reaches a public account by accident. A Social Publish Job is written only by
the approve action, after a second person has approved the post, and never by the author::

    Draft --submit--> Pending Approval --approve--> Approved --(outbox)--> Scheduled -> ...
      ^                      |
      +------send back-------+
    Draft, Pending Approval, Approved, Scheduled, Publishing --cancel--> Canceled

Roles, decided by Nik on 2026-09-22:

* **Marketing Team drafts.** It creates and edits posts, uploads media and submits them for
  approval. It grants nothing outside the Marketing module -- no customers, no invoices, no
  financials -- so a marketing hire given the ``Marketing`` role profile has the publishing
  surface and nothing else.
* **Marketing Manager approves,** and never a post they wrote or were the last to edit: approver
  and author are two people. ``outbox.enqueue`` checks the author half again, independently.
* **Only from a signed-in browser.** A request authenticated by a token -- an API key or an OAuth
  bearer, which is how Triton and the MCP connect -- is refused whatever roles it holds, and so is
  one with no request at all (the console, a background job). Frappe gives such a request a
  session id equal to the user's name (``frappe.set_user``), where a login gets a random one;
  that is the test (``signed_in_browser``). Triton's service account holds Marketing Manager, and
  this is what keeps it from approving. Resolving a publish job -- "it was published", "send it
  again" -- uses the same test, because both answers are about what is public.

Stopping is always allowed: anyone who may edit a post may cancel it, from anywhere. The safe
direction should never be the hard one.

Status, approver and approval time change only through the actions (``approval.py``) and the
outbox; an ordinary save that changes them is refused (``status_edit_problem``).

Pure: no frappe, so the bench-free CI tier tests every rule. ``approval.py`` holds the endpoints.
"""

import datetime

from erpnext_enhancements.marketing.publish import outbox

TEAM_ROLE = "Marketing Team"
APPROVER_ROLE = "Marketing Manager"
#: May resolve an Unconfirmed or Failed publish job (``sweeper.resolve_job``).
RESOLVER_ROLES = frozenset({"System Manager", APPROVER_ROLE})
#: Written only by the actions and the outbox, never by an ordinary save.
WORKFLOW_FIELDS = ("status", "approver", "approved_at")
#: A post can be canceled while something in it has not gone out yet.
CANCELABLE = frozenset(
	{
		outbox.POST_DRAFT,
		outbox.POST_PENDING_APPROVAL,
		outbox.POST_APPROVED,
		outbox.POST_SCHEDULED,
		outbox.POST_PUBLISHING,
	}
)
#: A post can be deleted only before anything was queued for it, or once canceled. (A canceled
#: post that has jobs is still refused, by Frappe's own link check.)
DELETABLE = frozenset({outbox.POST_DRAFT, outbox.POST_PENDING_APPROVAL, outbox.POST_CANCELED})


def signed_in_browser(session, authorization_header=None):
	"""Whether a request comes from a person's login rather than a token or a job.

	``session`` is ``frappe.session`` (``user`` and ``sid``). A login's session id is random; a
	token-authenticated request, a background job and the console all get ``sid == user``.
	"""
	user = (session or {}).get("user")
	sid = (session or {}).get("sid")
	if not user or user == "Guest" or authorization_header:
		return False
	return bool(sid) and sid not in ("Guest", user)


def status_of(post):
	return post.get("status") or outbox.POST_DRAFT


def _moment(value):
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value
	return datetime.datetime.fromisoformat(str(value).strip())


def status_edit_problem(before, after):
	"""Why an ordinary save may not write these workflow fields; ``None`` if it may.

	``before`` is the saved post (``None`` for a new one). A new post starts as a Draft with no
	approver; after that, status, approver and approval time are the actions' to change.
	"""
	if before is None:
		if status_of(after) != outbox.POST_DRAFT or after.get("approver") or after.get("approved_at"):
			return "A new post starts as a Draft, with no approver."
		return None
	for field in WORKFLOW_FIELDS:
		old, new = before.get(field), after.get(field)
		same = _moment(old) == _moment(new) if field == "approved_at" else (old or "") == (new or "")
		if not same:
			return (
				"Status, approver and approval time change only through the post's actions "
				"(Submit for Approval, Approve, Send Back, Cancel)."
			)
	return None


def submit_problems(post, content_problems):
	"""Why a post cannot go for approval; empty means it can.

	``content_problems`` is ``outbox.content_problems`` for the post: an approver is never asked
	to approve something the outbox would refuse to send.
	"""
	problems = []
	if status_of(post) != outbox.POST_DRAFT:
		problems.append(f"it is {status_of(post)}, not Draft")
	return problems + list(content_problems)


def approval_problems(post, user, roles, browser, unchanged):
	"""Why ``user`` cannot approve ``post``; empty means they can.

	``unchanged`` says the post is still the version the approver was looking at.
	"""
	problems = []
	if APPROVER_ROLE not in roles:
		problems.append("only a Marketing Manager can approve a post")
	if not browser:
		problems.append(
			"approvals are made from a signed-in browser; API keys, tokens and background jobs cannot approve"
		)
	if status_of(post) != outbox.POST_PENDING_APPROVAL:
		problems.append(f"it is {status_of(post)}, not Pending Approval")
	if user == post.get("owner"):
		problems.append("you wrote it, so someone else must approve it")
	elif user == post.get("modified_by"):
		problems.append("you made the latest change to it, so someone else must approve it")
	if not unchanged:
		problems.append("it changed after you opened it; reload it and review it again")
	return problems


def resolve_problems(roles, browser):
	"""Why the current user cannot resolve a publish job; empty means they can."""
	problems = []
	if not RESOLVER_ROLES & set(roles):
		problems.append("only a Marketing Manager or System Manager can resolve a publish job")
	if not browser:
		problems.append(
			"this is answered from a signed-in browser, by a person who checked the network; "
			"API keys, tokens and background jobs cannot answer it"
		)
	return problems


def send_back_problems(post):
	"""Why a post cannot go back to Draft."""
	if status_of(post) != outbox.POST_PENDING_APPROVAL:
		return [f"it is {status_of(post)}, not Pending Approval"]
	return []


def cancel_problems(post):
	"""Why a post cannot be canceled."""
	if status_of(post) not in CANCELABLE:
		return [f"it is {status_of(post)}; there is nothing left to stop"]
	return []


def delete_problems(post):
	"""Why a post cannot be deleted."""
	if status_of(post) not in DELETABLE:
		return [f"it is {status_of(post)}; cancel it instead, so the record of what went out stays"]
	return []


def refusal(post_name, action, problems):
	"""One sentence for the person who asked."""
	return f"{post_name} cannot be {action}: " + "; ".join(problems) + "."
