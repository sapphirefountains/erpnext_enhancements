# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Embeddings, over the Vertex AI REST API. **No SDK** — ADR 0004, and the host cannot install one.

The production host is a managed server whose deploy pipeline has **no ``pip install`` step at
all**, so this talks HTTP with ``requests``, exactly as ``stripe_payments`` and the QuickBooks
client do. Do not "fix" this by adding ``google-cloud-aiplatform``.

--------------------------------------------------------------------------------------
The two task types are not interchangeable, and using one for both is silently worse
--------------------------------------------------------------------------------------

``gemini-embedding-001`` takes a ``task_type``. Indexing a chunk is ``RETRIEVAL_DOCUMENT``;
embedding the question is ``RETRIEVAL_QUERY``. The model places asymmetric queries and
documents in the same space *because* it is told which is which; using ``RETRIEVAL_DOCUMENT``
for both is not an error and does not fail — it just retrieves worse, in a way no test that
checks "did it return something" will catch.

--------------------------------------------------------------------------------------
The re-normalisation rule, which is the documented number-one mistake
--------------------------------------------------------------------------------------

The model returns unit-length vectors **only at its native dimension**. Any other
``output_dimensionality`` is a Matryoshka truncation, and a truncated vector is **not** unit
length. Cosine similarity over non-normalised vectors is not cosine similarity — it is a dot
product weighted by whatever magnitude survived the cut.

So every vector is put through :func:`chat.retrieval.vectors.normalise` on the way out of this
module, whatever the dimension, and the storage layer asserts it on the way back in. Skipping
it produces plausible-looking, subtly wrong retrieval: the worst failure shape available,
because it looks like the feature working.

--------------------------------------------------------------------------------------
Failure posture
--------------------------------------------------------------------------------------

An embedding failure is an **operational** event, not a security one, so it degrades rather
than raising through to the user: :func:`embed_query` returns ``[]``, the semantic tier
contributes nothing, and retrieval answers from the lexical tier. An answer from exact matches
beats no answer.

Every exception raised out of here uses ``raise … from None``. A bare re-raise out of a
background job publishes the failing frames' locals into the Error Log, and those frames hold
an ``Authorization: Bearer`` header. This app has already leaked private key material exactly
that way once.
"""

from __future__ import annotations

import hashlib
from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.retrieval import assemble, vectors

#: Vertex AI's regional host. A constant rather than a settings field: the region has to match
#: the project's model availability, it is not something an operator retunes, and a wrong value
#: is a 404 rather than a degradation.
DEFAULT_LOCATION: str = "us-central1"

DEFAULT_MODEL: str = "gemini-embedding-001"
DEFAULT_DIM: int = 1_536

TASK_DOCUMENT: str = "RETRIEVAL_DOCUMENT"
TASK_QUERY: str = "RETRIEVAL_QUERY"

#: Vertex's own per-request instance cap for this model family. Batching past it is a 400, and
#: a 400 in a backfill loop retries forever against an input that will never be accepted.
MAX_BATCH: int = 25

#: Query embeddings are cached: the same question asked twice in a thread is the normal case,
#: and an embedding call is the most expensive part of a cheap turn. Document embeddings are
#: not cached — they are written to the chunk row, which *is* the cache.
QUERY_CACHE_TTL: int = 3_600


class EmbeddingError(Exception):
	"""An embedding call failed. Never carries the request body or a token."""


def embed_query(query: str) -> list[float]:
	"""The question's vector, normalised, cached. ``[]`` when the tier cannot run.

	Returns a plain list rather than a numpy array so the cache round-trip is a value the
	Redis wrapper can pickle without carrying a numpy version dependency into the cache — a
	cached array becomes unreadable across a numpy upgrade, and the symptom is a cache that
	silently never hits.
	"""
	text = (query or "").strip()
	if not text:
		return []

	model, dim = _model_and_dim()
	key = assemble.query_embedding_cache_key(hashlib.sha256(text.encode("utf-8")).hexdigest(), model, dim)

	try:
		cached = frappe.cache().get_value(key)
		if cached:
			return list(cached)
	except Exception:
		# A deploy FLUSHDBs this cache and the eviction policy is LRU, so a miss is normal
		# and an unreachable cache must not be an outage.
		pass

	try:
		vectors_out = _embed([text], task_type=TASK_QUERY)
	except EmbeddingError:
		return []
	if not vectors_out:
		return []

	result = vectors_out[0]
	try:
		frappe.cache().set_value(key, result, expires_in_sec=QUERY_CACHE_TTL)
	except Exception:
		pass
	return result


def embed_documents(texts: list[str]) -> list[list[float]]:
	"""Vectors for chunk bodies, in input order, normalised.

	**Raises** on failure, unlike :func:`embed_query`. The caller is the indexer, which records
	the failure on the chunk row and stops retrying after a few attempts — so swallowing here
	would leave a chunk permanently unembedded with nothing recording why.
	"""
	if not texts:
		return []
	if len(texts) > MAX_BATCH:
		raise EmbeddingError(
			f"{len(texts)} texts exceeds the per-request cap of {MAX_BATCH}. Batch upstream; "
			"a request over the cap is a 400, and a 400 inside a retry loop never succeeds."
		)
	return _embed(texts, task_type=TASK_DOCUMENT)


def _embed(texts: list[str], *, task_type: str) -> list[list[float]]:
	import requests

	from erpnext_enhancements.chat.gchat import auth

	model, dim = _model_and_dim()
	project = (_setting("google_project_id") or "").strip()
	if not project:
		raise EmbeddingError("Chat Settings has no Google project id, so Vertex AI cannot be reached.")

	timeout = cint(_setting("http_timeout_seconds")) or 30
	url = (
		f"https://{DEFAULT_LOCATION}-aiplatform.googleapis.com/v1/projects/{project}"
		f"/locations/{DEFAULT_LOCATION}/publishers/google/models/{model}:predict"
	)
	payload: dict[str, Any] = {
		"instances": [{"content": text, "task_type": task_type} for text in texts],
		"parameters": {"outputDimensionality": dim},
	}

	try:
		token = auth.get_vm_access_token(timeout)
		response = requests.post(
			url,
			json=payload,
			headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
			timeout=timeout,
		)
	except Exception as exc:
		# `from None` deliberately: the frames above hold the Authorization header.
		raise EmbeddingError(f"Vertex AI embedding request failed: {exc.__class__.__name__}") from None

	if response.status_code >= 400:
		# The body can carry the offending input back. Status only.
		raise EmbeddingError(f"Vertex AI embedding returned HTTP {response.status_code}") from None

	try:
		predictions = response.json().get("predictions") or []
	except Exception:
		raise EmbeddingError("Vertex AI embedding returned a body that is not JSON") from None

	out: list[list[float]] = []
	for prediction in predictions:
		values = ((prediction or {}).get("embeddings") or {}).get("values") or []
		if not values:
			raise EmbeddingError("Vertex AI embedding returned a prediction with no values") from None
		# Normalised here, unconditionally, whatever the dimension. See the module docstring:
		# a truncated vector is not unit length, and cosine over non-unit vectors is a
		# different function that returns plausible numbers.
		out.append([float(value) for value in vectors.normalise(values)])

	if len(out) != len(texts):
		raise EmbeddingError(
			f"Vertex AI returned {len(out)} embeddings for {len(texts)} inputs. The mapping is "
			"positional, so a partial response cannot be assigned to chunks safely."
		) from None
	return out


def _model_and_dim() -> tuple[str, int]:
	model = (_setting("embedding_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
	dim = cint(_setting("embedding_dim")) or DEFAULT_DIM
	return model, dim


def _setting(fieldname: str) -> Any:
	try:
		return frappe.db.get_single_value("Chat Settings", fieldname)
	except Exception:
		return None
