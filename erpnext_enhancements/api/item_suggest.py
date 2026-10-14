"""The guided Item flow's AI suggest step (ER-2026-1320546, on form kit P8.1's TASK-2026-02790).

Parker Bailey (purchasing): "An AI assisted way to create new item codes for items that don't have
codes." Nik, 2026-10-14: an AI *suggest* step on top of the rule-based guided Item flow; the AI does
not replace the rules. :func:`suggest_item` is the one endpoint. The guided page's *Describe it*
card (``public/js/guided_item/extension.js``) and the ``item_naming_suggest`` assistant tool both
call :func:`run_suggestion`.

What it keeps to:

* **It writes nothing.** It reads the catalog, asks the model once and returns fields. No Item, no
  File, no price. The person accepts each field into the step's own builder, and the page's naming
  check and save work as they always have. The only rows written are the shared Gemini client's AI
  Model Usage token row and, when the call fails, one Error Log row naming the exception's class.
* **It never picks a PDT- or SRV- number, or a vendor part number.** A product or service
  suggestion is the family alone: the person takes the number from ``api.item_flow.code_blocks``.
  A part number is the one the person typed or wrote in the description; one read off a photo is
  marked for them to check. See ``inventory_enhancements/item_suggest_rules.py``.
* **The rules have the last word.** The suggestion goes through ``item_naming_rules`` (the
  checks, the CATEGORY's Appendix A corrections, and the new-Item guard's two findings) and
  through Stock Scan's ``preview_new_item``, the same preview the step's naming check shows, so
  an existing Item that looks the same is named before anyone accepts anything.
* **Failure is graceful.** The AI is not set up, it timed out, it answered something that is not
  the JSON asked for, or it suggested nothing usable: ``{"ok": False, "message": ...}`` in plain
  words, and the rule-based helper below the card works as before. The Error Log row carries the
  exception's class and a short message, never the prompt or the photo (``frappe.get_traceback``
  is not used: Frappe renders local variables, and both are locals).
* **The photo is checked by its bytes.** JPEG, PNG, WebP or HEIC, at most 4 MB decoded, sent as
  base64 (``image``) or named as a File the caller can read (``file_url``). The page scales a phone
  photo to 1600px before it sends it, and nothing is stored.

Gated on Item create, as ``api.item_flow``'s preview and blocks are, and rate-limited per user.
Indentation is tabs (new file).
"""

import base64

import frappe
from frappe import _
from frappe.utils import cstr

from erpnext_enhancements.inventory_enhancements import item_suggest_rules as rules

#: Calls a person may make, and over how long. Counted per user (``@rate_limit`` keys by IP, which
#: is one budget for the whole office).
RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 3600

#: A person is waiting at the screen: a lighter think than the drafting features' HIGH, and a
#: timeout well inside the web worker's.
THINKING_LEVEL = "MEDIUM"
TIMEOUT_SECONDS = 60


def ai_configured():
	"""Is the Google service account the Gemini client signs in with set up? A boolean, never the key."""
	try:
		from erpnext_enhancements.google_drive.drive_utils import get_service_account_info

		return bool(get_service_account_info())
	except Exception:
		return False


def _enforce_rate():
	"""False when this user has used their suggestions for the window. A cache that cannot count
	(infrastructure, not the buyer's fault) lets the call through."""
	try:
		key = frappe.cache.make_key(f"ee_item_suggest:{frappe.session.user}")
		count = frappe.cache.incrby(key, 1)
		if count == 1:
			frappe.cache.expire(key, RATE_WINDOW_SECONDS)
		return count <= RATE_LIMIT
	except Exception:
		return True


def _log(exc):
	"""An Error Log row with the exception's class and a short message, never the prompt or the
	photo. Keyword arguments: v16 reads a positional first argument as the title."""
	try:
		frappe.log_error(
			title="Item AI suggestion failed",
			message=f"{type(exc).__name__}: {rules.plain_text(str(exc), 400)}",
		)
	except Exception:
		pass


def _answer(ok, message="", **extra):
	return dict({"ok": bool(ok), "message": message}, **extra)


def _photo(image=None, file_url=None):
	"""``({"mime_type", "data"}, None)`` for the photo, or ``(None, message)``. A File is read only
	when the caller may read it; its bytes, not its name, say what it is."""
	raw = None
	if image:
		try:
			raw = rules.decode_image(image)
		except ValueError as exc:
			return None, str(exc)
	elif file_url:
		url = cstr(file_url).strip()
		names = frappe.get_all("File", filters={"file_url": url}, pluck="name", limit=1) if url else []
		if not names:
			return None, rules.MSG_PHOTO_UNREADABLE
		doc = frappe.get_doc("File", names[0])
		doc.check_permission("read")
		if (doc.get("file_size") or 0) > rules.IMAGE_MAX_BYTES:
			return None, rules.MSG_PHOTO_SIZE
		try:
			# No encodings: v16's File.get_content otherwise tries utf-8, windows-1250 and
			# windows-1252 in turn, and a JPEG that happens to decode as one comes back a str.
			raw = doc.get_content(encodings=())
		except Exception:
			return None, rules.MSG_PHOTO_UNREADABLE
		if not isinstance(raw, bytes | bytearray):
			return None, rules.MSG_PHOTO_UNREADABLE
	problem = rules.image_problem(raw)
	if problem:
		return None, problem
	return {"mime_type": rules.sniff_image(raw), "data": base64.b64encode(raw).decode("ascii")}, None


def read_context(seed):
	"""What the prompt and the checks need from the site: ``{"categories", "item_groups",
	"uoms", "neighbours", "codes"}``.

	The item groups are the tree's leaves without its root, and the units are the ones live Items
	use, so the model can only pick what a buyer already picks from. ``neighbours`` are the catalog
	names that share the most (rarest-first) words with ``seed``, never their codes; ``codes`` is
	every Item code, read as the new-Item guard reads it (a code is taken whoever can see it).
	"""
	from erpnext_enhancements.inventory_enhancements import item_flow_rules, item_naming
	from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming

	rows, _meta = item_naming.read_corpus(include_deleted=False)
	groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
	neighbours = [
		row.get("item_name")
		for row in naming.similar_records(seed, rows, limit=rules.NEIGHBOURS, min_score=0.05)
		if row.get("item_name")
	]
	return {
		"categories": item_flow_rules.approved_categories(),
		"item_groups": sorted(g for g in groups if g and g != naming.ROOT_ITEM_GROUP),
		"uoms": sorted({row.get("stock_uom") for row in rows if row.get("stock_uom")}),
		"neighbours": neighbours,
		"codes": frappe.get_all("Item", pluck="name"),
	}


def _ask_model(prompt, photo):
	"""One call to the app's Gemini client; raises whatever it raises. Imported per call, as the
	other drafting features do, so an import problem is a message and not a broken page."""
	from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai

	text, _thoughts = generate_content_with_vertex_ai(
		prompt,
		rules.SYSTEM_PROMPT,
		None,
		feature=rules.FEATURE,
		images=[photo] if photo else None,
		response_mime_type="application/json",
		thinking_level=THINKING_LEVEL,
		timeout=TIMEOUT_SECONDS,
	)
	return text


def _preview(composed):
	"""Stock Scan's preview of the composed Item: the same answer the step's naming check gives
	(``api.item_flow.preview_item``). Advice only when it cannot run."""
	from erpnext_enhancements.api.stock_scan import preview_new_item

	try:
		return preview_new_item(
			composed["item_code"], composed["item_name"], composed["item_group"], composed["stock_uom"]
		)
	except Exception as exc:
		_log(exc)
		return {"checked": False}


def run_suggestion(description=None, vendor_part_no=None, image=None, file_url=None):
	"""Suggest a new Item's code family, POL-0602 name, group and unit. **Writes nothing.** The
	caller has checked who may ask (:func:`suggest_item`, the assistant tool).

	Answers ``{"ok", "message", "note", "fields", "item_code", "item_name", "family",
	"number_from_blocks", "missing", "normalized", "dropped", "review", "check"}``. ``fields`` are
	the page's rows: ``{"key", "label", "value", "reason", "confirm", "note", "segment"?, "verdict"?}``.
	``item_code`` is "" for a product or a service, whose number the person takes from the block list
	``number_from_blocks`` names. ``review`` is the naming rules and the guard on the composed Item;
	``check`` is ``preview_new_item`` (an existing Item with that code, similar Items, the guard's
	refusal). When the AI cannot help, ``ok`` is false and ``message`` says why in plain words.
	"""
	text = rules.scrub_description(description)
	part = rules.vendor_code(rules.plain_text(vendor_part_no, rules.PART_MAX * 2))[: rules.PART_MAX]
	if not text and not part and not image and not file_url:
		return _answer(False, _(rules.MSG_EMPTY))
	photo = None
	if image or file_url:
		photo, problem = _photo(image, file_url)
		if problem:
			return _answer(False, _(problem))
	if not ai_configured():
		return _answer(False, _(rules.MSG_NOT_SET_UP))
	if not _enforce_rate():
		return _answer(False, _(rules.MSG_BUSY), rate_limited=1)

	try:
		context = read_context(" ".join(bit for bit in (text, part) if bit))
		prompt = rules.build_prompt(text, part, context, has_photo=bool(photo))
		reply = _ask_model(prompt, photo)
	except Exception as exc:
		_log(exc)
		return _answer(False, _(rules.failure_message(exc)))

	data = rules.parse_answer(reply)
	if data is None:
		_log(ValueError("The model's reply was not a JSON object"))
		return _answer(False, _(rules.MSG_BAD_ANSWER))
	checked = rules.validate_suggestion(
		data,
		description=text,
		vendor_part_no=part,
		has_photo=bool(photo),
		item_groups=context["item_groups"],
		uoms=context["uoms"],
	)
	normalized = rules.normalize_category(checked)
	composed = rules.compose(checked)
	if not composed["family"] and not composed["item_name"]:
		return _answer(False, _(rules.MSG_NOTHING), dropped=checked["dropped"])
	review = rules.review(composed, existing_codes=context["codes"])
	check = _preview(composed) if (composed["item_code"] or composed["item_name"]) else {"checked": False}
	return _answer(
		True,
		note=_(rules.REVIEW_NOTE),
		fields=rules.field_rows(checked, review),
		item_code=composed["item_code"],
		item_name=composed["item_name"],
		family=composed["family"],
		number_from_blocks=composed["number_from_blocks"],
		missing=checked["missing"],
		normalized=normalized,
		dropped=checked["dropped"],
		review=review,
		check=check,
	)


@frappe.whitelist(methods=["POST"])
def suggest_item(description=None, vendor_part_no=None, image=None, file_url=None):
	"""The *Describe it* card's Suggest: a plain-words ``description``, an optional
	``vendor_part_no`` and an optional photo (``image``, base64 or a ``data:`` URL, or ``file_url``,
	a File the caller can read). POST, because the photo does not fit an address and every call
	costs a model call. For whoever may create an Item. **Writes nothing**: see
	:func:`run_suggestion` for the answer.
	"""
	frappe.has_permission("Item", "create", throw=True)
	return run_suggestion(description, vendor_part_no, image=image, file_url=file_url)
