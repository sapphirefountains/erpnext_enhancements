import frappe
from frappe import _


@frappe.whitelist()
def get_comments(reference_doctype, reference_name):
	"""
	Retrieves comments for a given document, along with user details.
	"""
	if not reference_doctype or not reference_name:
		return []

	if not frappe.has_permission(reference_doctype, "read", reference_name):
		frappe.throw(_("You do not have permission to view this document."))

	# Fetch standard comments linked to the document
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"comment_type": "Comment"
		},
		fields=["name", "content", "owner", "creation"],
		order_by="creation desc"
	)

	# Get user details in bulk to avoid N+1 queries
	user_ids = [c.get("owner") for c in comments]
	if not user_ids:
		return []

	user_details = frappe.get_all(
		"User",
		filters={"name": ["in", list(set(user_ids))]},
		fields=["name", "full_name", "user_image"],
	)
	user_map = {u.get("name"): u for u in user_details}

	# Combine comment data with user details
	for comment in comments:
		user = user_map.get(comment.get("owner"))
		if user:
			comment["full_name"] = user.get("full_name")
			comment["user_image"] = user.get("user_image")
		else:
			comment["full_name"] = comment.get("owner")
			comment["user_image"] = None

	return comments


@frappe.whitelist()
def add_comment(reference_doctype, reference_name, comment_text):
	"""
	Adds a new comment to a document.
	"""
	if not reference_doctype or not reference_name or not comment_text:
		frappe.throw(_("Reference document and comment text are required."))

	if not frappe.has_permission(reference_doctype, "read", reference_name):
		frappe.throw(_("You do not have permission to view this document."))

	doc = frappe.new_doc("Comment")
	doc.comment_type = "Comment"
	doc.reference_doctype = reference_doctype
	doc.reference_name = reference_name
	doc.content = comment_text
	doc.insert(ignore_permissions=True)

	# Refetch the comment to include user details for the frontend
	user_details = frappe.get_doc("User", doc.owner)

	return {
		"name": doc.name,
		"content": doc.content,
		"owner": doc.owner,
		"creation": doc.creation,
		"full_name": user_details.full_name,
		"user_image": user_details.user_image
	}


@frappe.whitelist()
def delete_comment(comment_name):
	"""
	Deletes a comment if the user is the owner.
	"""
	if not comment_name:
		frappe.throw(_("Comment name is required."))

	try:
		comment = frappe.get_doc("Comment", comment_name)

		if comment.owner != frappe.session.user:
			frappe.throw(_("You are not allowed to delete this comment."))

		comment.delete(ignore_permissions=True)
		return {"success": True}
	except frappe.PermissionError:
		frappe.throw(_("You are not allowed to delete this comment."))
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Comment Deletion Failed")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def update_comment(comment_name, comment_text):
	if not comment_name or not comment_text:
		frappe.throw(_("Comment name and comment text are required."))

	try:
		comment = frappe.get_doc("Comment", comment_name)

		if comment.owner != frappe.session.user:
			frappe.throw(_("You are not allowed to edit this comment."))

		comment.content = comment_text
		comment.save(ignore_permissions=True)

		user_details = frappe.get_doc("User", comment.owner)

		return {
			"name": comment.name,
			"content": comment.content,
			"owner": comment.owner,
			"creation": comment.creation,
			"full_name": user_details.full_name,
			"user_image": user_details.user_image
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error updating comment")
		return {"error": str(e)}
