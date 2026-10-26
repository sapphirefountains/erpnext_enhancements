"""Signed copy — one click from a signed contract to the document that was signed.

Before v1.744.0 the only way to an e-signed contract's PDF was the form's Attachments sidebar.
``esign/lifecycle._build_signed_pdf`` attaches a private ``<name>-signed.pdf`` and records it on
the Contract Signature Request as ``signed_document``, and nothing in the Desk read that field. A
contract marked signed on paper has no PDF at all, only the copy frozen in ``executed_html`` when
it was marked (v1.611.0), and nothing said whether anybody had kept a scan of the paper.

:func:`signed_copy` answers "what does Signed copy open?" for one contract, in this order:

1. the latest Signed request's ``signed_document``, the PDF the customer was emailed;
2. an attachment named by the signed-copy convention (``<name>-signed…``): the e-sign PDF when
   the request has lost its link, or a paper scan filed with :func:`record_signed_scan`;
3. no file: the executed copy, read on screen through the contract viewer, with a note saying
   no signed scan is on file and, for a Contract Manager, an Attach scan shortcut.

A file is offered only when this reader could download it. Each candidate goes through frappe's
own ``find_file_by_url``, the same check ``/private/files/…`` makes before it serves a byte
(``frappe/utils/response.py:download_private_file`` at v16.53.0). The button never hands anyone
a link that answers 403, and it never serves the file itself, so the private-file rules stay
frappe's.

The two forms get the answer in ``__onload`` (``ProjectContract.onload`` and the maintenance
contract's), so the click opens the PDF in the same gesture. A window opened after a round trip
is what popup blockers stop, Safari's first. The list view's button has no onload and fetches
:func:`get_signed_copy` on click instead.
"""

import os

import frappe
from frappe import _

# What a signed copy may be: the PDF e-signing builds, or a scan or phone photo of the paper.
SCAN_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")

# Who may file the scan: the roles that may mark a contract signed (v1.615.0).
MANAGER_ROLES = frozenset({"System Manager", "Contract Manager"})


def file_stem(contract_name):
	"""The prefix of every signed-copy file name: ``_build_signed_pdf``'s own convention."""
	return str(contract_name or "").replace("/", "-") + "-signed"


def is_signed_copy_name(file_name, contract_name):
	"""Whether an attachment's name marks it as this contract's signed copy."""
	name = (file_name or "").lower()
	return name.startswith(file_stem(contract_name).lower()) and name.endswith(SCAN_EXTENSIONS)


def is_scan_type(file_name):
	"""Whether a file could be a signed copy at all: a PDF or a picture a browser shows."""
	return (file_name or "").lower().endswith(SCAN_EXTENSIONS)


def pick_signed_file(request_document, attachments, contract_name, downloadable):
	"""The signed copy to open, or None.

	Pure ordering, so the rules can be tested without a site. The caller supplies the rows and
	the permission check.

	:param request_document: the latest Signed request's ``signed_document`` URL, or None.
	:param attachments: File rows attached to the contract, newest first.
	:param downloadable: ``file_url -> truthy`` when this reader could download that file.
	:returns: ``{"file_url", "file_name", "source"}`` or None.
	"""
	if request_document and downloadable(request_document):
		return {
			"file_url": request_document,
			"file_name": os.path.basename(request_document),
			"source": "signature_request",
		}
	for row in attachments or []:
		file_url = row.get("file_url")
		if file_url and is_signed_copy_name(row.get("file_name"), contract_name) and downloadable(file_url):
			return {"file_url": file_url, "file_name": row.get("file_name"), "source": "attachment"}
	return None


def signed_copy(contract):
	"""What the Signed copy button opens for ``contract``, a Project Contract document.

	Keys: ``signed``; ``route`` ("esign" when a Signature Request executed it, else "paper");
	``file_url``/``file_name``/``source`` when there is a file to open; ``executed`` when there
	is no file but an executed copy to read; ``can_attach`` when this reader may file a scan;
	``attachments``, the other PDFs and pictures already attached, when a scan could be one of
	them.

	Never raises: it runs inside the form's onload, and a contract that will not open is worse
	than a button that says it found nothing.
	"""
	name = contract.get("name")
	result = {
		"name": name,
		"signed": 1 if contract.get("status") == "Signed" else 0,
		"route": None,
		"signed_by": contract.get("signed_by"),
		"signed_on": str(contract.get("signed_on") or "") or None,
		"file_url": None,
		"file_name": None,
		"source": None,
		"executed": 0,
		"can_attach": 0,
		"attachments": [],
	}
	if not result["signed"]:
		return result
	try:
		request = _signed_request(name)
		result["route"] = "esign" if request else "paper"
		attachments = _attachments(name)
		picked = pick_signed_file(
			request.get("signed_document") if request else None, attachments, name, _downloadable
		)
		if picked:
			result.update(picked)
			return result

		result["executed"] = 1 if _has_executed_copy(name) else 0
		if result["route"] == "paper" and _may_file_scan(contract):
			result["can_attach"] = 1
			result["attachments"] = [
				{"name": row.get("name"), "file_name": row.get("file_name"), "file_url": row.get("file_url")}
				for row in attachments
				if is_scan_type(row.get("file_name")) and _downloadable(row.get("file_url"))
			]
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Project Contract: signed copy lookup failed")
	return result


def signed_copy_for(project_contract):
	""":func:`signed_copy` for a contract another form links to, or None.

	None when there is no link, the contract is not Signed, or this reader cannot read it: the
	maintenance contract's readers are not all the agreement's. Never raises (onload).
	"""
	if not project_contract:
		return None
	try:
		if frappe.db.get_value("Project Contract", project_contract, "status") != "Signed":
			return None
		if not frappe.has_permission("Project Contract", "read", project_contract):
			return None
		return signed_copy(frappe.get_doc("Project Contract", project_contract))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Project Contract: signed copy lookup failed")
		return None


@frappe.whitelist()
def get_signed_copy(project_contract):
	"""The Signed copy button's answer for one contract, for the list view (forms use onload)."""
	contract = frappe.get_doc("Project Contract", project_contract)
	contract.check_permission("read")
	return signed_copy(contract)


@frappe.whitelist(methods=["POST"])
def record_signed_scan(project_contract, file):
	"""File an attachment as a paper-signed contract's signed copy.

	Filing it means renaming it to the convention, ``<name>-signed-scan.pdf``. The attachments
	sidebar, the downloaded file's name and :func:`signed_copy` then all agree on which file it
	is, with no new field on the contract. It is made private, as the e-signed PDF is: it carries
	signatures and prices. A timeline comment records who filed it, as "Mark as Signed" leaves one
	for who marked it.

	Refused for a contract signed online, whose signed PDF is the record, and for anyone who
	cannot mark a contract signed.
	"""
	contract = frappe.get_doc("Project Contract", project_contract)
	contract.check_permission("write")
	if not set(frappe.get_roles()) & MANAGER_ROLES:
		frappe.throw(_("Only a Contract Manager can file a contract's signed copy."), frappe.PermissionError)
	if contract.get("status") != "Signed":
		frappe.throw(_("{0} is not signed yet.").format(project_contract))
	if _signed_request(project_contract):
		frappe.throw(
			_("{0} was signed online. Its signed PDF is the record, and a scan cannot replace it.").format(
				project_contract
			)
		)

	file_doc = frappe.get_doc("File", file)
	if (file_doc.get("attached_to_doctype"), file_doc.get("attached_to_name")) != (
		"Project Contract",
		project_contract,
	):
		frappe.throw(_("That file is not attached to {0}.").format(project_contract))
	ext = os.path.splitext(file_doc.get("file_name") or file_doc.get("file_url") or "")[1].lower()
	if ext not in SCAN_EXTENSIONS:
		frappe.throw(_("A signed copy must be a PDF or a picture (PNG or JPEG)."))

	file_doc.file_name = f"{file_stem(project_contract)}-scan{ext}"
	file_doc.is_private = 1
	file_doc.save(ignore_permissions=True)
	contract.add_comment(
		"Comment",
		_("Signed copy filed: {0}, a scan of the paper signature, by {1}.").format(
			file_doc.file_name, frappe.session.user
		),
	)
	return {"name": file_doc.name, "file_name": file_doc.file_name, "file_url": file_doc.file_url}


def _signed_request(name):
	"""The Signature Request that executed the contract (newest), as a dict, or None."""
	return frappe.db.get_value(
		"Contract Signature Request",
		{"project_contract": name, "status": "Signed"},
		["name", "signed_document"],
		as_dict=True,
		order_by="signed_on desc",
	)


def _attachments(name):
	"""Files attached to the contract, newest first."""
	return frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Project Contract", "attached_to_name": name, "is_folder": 0},
		fields=["name", "file_name", "file_url"],
		order_by="creation desc",
	)


def _downloadable(file_url):
	"""Whether this reader could download ``file_url``: the download's own check, not a copy."""
	if not file_url:
		return False
	from frappe.core.doctype.file.utils import find_file_by_url

	try:
		return bool(find_file_by_url(file_url))
	except Exception:
		return False


def _has_executed_copy(name):
	from erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract import (
		_executed_html,
	)

	return bool(_executed_html(name))


def _may_file_scan(contract):
	return bool(set(frappe.get_roles()) & MANAGER_ROLES) and contract.has_permission("write")
