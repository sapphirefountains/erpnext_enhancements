# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Client for Triton's Document AI extraction service.

Extraction is delegated to Triton (FastAPI) rather than calling Google
Document AI from ERPNext directly: Triton holds the GCP credentials and the
per-document-type processor map, and exposes the same capability to its chat
assistant. This module POSTs the raw document bytes to Triton's
``/api/v1/document-ai/extract`` endpoint and returns the normalized fields.

The gateway URL comes from Accounting Intake Settings, falling back to the
shared ``Triton Settings`` gateway; the service secret (if set) is sent in the
``X-Triton-Service-Secret`` header for server-to-server auth."""

import frappe
import requests

EXTRACT_PATH = "/api/v1/document-ai/extract"


def _gateway_and_secret(settings=None):
	settings = settings or frappe.get_cached_doc("Accounting Intake Settings")
	gateway = (settings.get("triton_gateway_url") or "").rstrip("/")
	if not gateway:
		gateway = (frappe.db.get_single_value("Triton Settings", "gateway_url") or "").rstrip("/")
	secret = settings.get_password("triton_service_secret", raise_exception=False)
	return gateway, secret


def extract_document(content, mime_type, document_type, *, filename="document", settings=None, timeout=120):
	"""POST a document to Triton and return its normalized extraction.

	Returns a dict like ``{"entities": {...}, "confidence": <0..1>,
	"text": "...", "processor": "..."}``. Raises on misconfiguration or a
	non-2xx response so the caller can mark the intake Failed."""
	gateway, secret = _gateway_and_secret(settings)
	if not gateway:
		frappe.throw("Triton gateway URL is not configured (Accounting Intake Settings / Triton Settings).")

	headers = {}
	if secret:
		headers["Authorization"] = f"Bearer {secret}"

	resp = requests.post(
		f"{gateway}{EXTRACT_PATH}",
		headers=headers,
		files={"file": (filename, content, mime_type or "application/octet-stream")},
		data={"document_type": document_type or "Unknown"},
		timeout=timeout,
	)
	resp.raise_for_status()
	return resp.json()
