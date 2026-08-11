# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Vector storage and scoring, behind a two-method adapter so the backend is a one-file swap.

**The decision is measured, not assumed.** Production MariaDB is ``10.11.18`` on Debian 12 —
read from a live ``SELECT VERSION()`` and corroborated by probing the feature surface: zero
``VEC%`` routines, zero vector plugins. There is no ``VECTOR`` type and there are no ``VEC_*``
functions, so the "on 11.8 this may flip" branch of the design is closed against the flip.
And ``numpy`` is already installed in the production bench, which is decisive on a host whose
deploy pipeline has **no ``pip install`` step at all**.

So: embeddings live in a column, and cosine similarity is computed in-process over the
**permission-filtered** candidate set. Filtered is what makes it viable — a corpus-wide scan
of a hundred thousand chunks would be visible in a web worker's profile, while a few thousand
is microseconds. The revisit triggers are therefore written against the *filtered* count.

--------------------------------------------------------------------------------------
Why the storage is base64 rather than a BLOB
--------------------------------------------------------------------------------------

The decision's word is "BLOB" and **Frappe has no BLOB fieldtype**. The column is a
``Long Text`` holding base64 of the raw ``float32`` bytes: a measured 33% storage overhead,
with a raw ``longblob`` by patch recorded as the optimisation if volume ever justifies it.
That satisfies the intent exactly — the vector is opaque bytes the database never interprets —
without inventing a fieldtype.

--------------------------------------------------------------------------------------
The normalisation rule that silently corrupts everything if skipped
--------------------------------------------------------------------------------------

``gemini-embedding-001`` returns vectors normalised **only at its native dimension**. Any
other output dimension is a Matryoshka truncation, and a truncated vector is **not** unit
length. Cosine similarity computed on non-normalised vectors is not cosine similarity; it is
a dot product weighted by whatever magnitude survived truncation.

This is the documented number-one mistake with this model family and its failure shape is the
worst available: retrieval keeps working, keeps returning plausible chunks, and is subtly
wrong in a way no test that only checks "did it return something" will ever catch. So
:func:`normalise` is applied on the way **in** and asserted on the way **out**.

--------------------------------------------------------------------------------------
Why the adapter takes a loader instead of running its own query
--------------------------------------------------------------------------------------

The design's signature is ``search(allowed_rooms, query_vec, limit)``. Here the candidate rows
arrive through a ``candidate_loader`` the gate supplies, and the reason is the invariant that
outranks the signature: **all chat SQL lives in the gate**, and a source-level test enforces
it. A backend that ran its own ``SELECT`` would be a second place the permission filter could
be forgotten, which is the one thing this phase must not have.

The consequence is stated rather than hidden: a future MariaDB-native vector backend cannot
simply be dropped in behind this interface — its query belongs in the gate too, or that rule
gets revisited deliberately. The adapter still buys what it was for, which is that the
*scoring* implementation is one file.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

#: Rows the gate hands over: ``(chunk_name, room, embedding_b64, embedding_dim)``.
CandidateRow = tuple[str, str, str, int]

#: ``() -> iterable of CandidateRow``. Supplied by the gate, already permission-filtered.
CandidateLoader = Callable[[], Iterable[CandidateRow]]


class VectorBackendError(Exception):
	"""Raised for a malformed stored vector. Never carries the vector."""


@dataclass(frozen=True)
class Hit:
	chunk: str
	room: str
	similarity: float


def encode_vector(values: Sequence[float]) -> str:
	"""``float32`` little-endian, base64. The one on-disk representation.

	``float32`` rather than ``float64`` halves the storage for a precision loss that is
	irrelevant to a similarity ranking — the difference between two chunks' scores is orders
	of magnitude larger than ``float32``'s error. Little-endian explicitly, not native:
	``numpy``'s native order is the machine's, and a vector written on one architecture must
	be readable on another.
	"""
	numpy = _numpy()
	array = numpy.asarray(list(values), dtype="<f4")
	return base64.b64encode(array.tobytes()).decode("ascii")


def decode_vector(encoded: str, expected_dim: int | None = None) -> Any:
	"""Base64 → ``numpy`` ``float32`` array.

	``expected_dim`` is checked rather than trusted. A dimension mismatch means the row was
	written by a different embedding configuration, and scoring it against the current query
	vector produces a number with no meaning that looks exactly like a number with meaning.
	"""
	numpy = _numpy()
	try:
		raw = base64.b64decode(encoded or "", validate=True)
	except Exception as exc:
		raise VectorBackendError(f"stored vector is not valid base64 ({exc.__class__.__name__})") from None
	if not raw or len(raw) % 4:
		raise VectorBackendError(f"stored vector is {len(raw)} bytes, not a whole number of float32")
	array = numpy.frombuffer(raw, dtype="<f4")
	if expected_dim is not None and array.shape[0] != expected_dim:
		raise VectorBackendError(f"stored vector has {array.shape[0]} dimensions, expected {expected_dim}")
	return array


def normalise(values: Any) -> Any:
	"""Unit-length, or the zero vector unchanged.

	Called on every vector in and every vector out. A zero vector is returned as-is rather
	than divided: it has no direction, so it has no cosine similarity to anything, and
	dividing would produce ``nan`` which then propagates silently through a ranking and sorts
	unpredictably.
	"""
	numpy = _numpy()
	array = numpy.asarray(values, dtype="<f4")
	norm = float(numpy.linalg.norm(array))
	if norm == 0.0:
		return array
	return (array / norm).astype("<f4")


def is_normalised(values: Any, tolerance: float = 1e-3) -> bool:
	"""Whether a vector is already unit length, within tolerance.

	The tolerance is loose on purpose: ``float32`` accumulation over a few thousand
	dimensions drifts, and a strict check would reject vectors that are fine while doing
	nothing about the ones that are genuinely un-normalised (whose norm is off by tens of
	percent, not by ``1e-6``).
	"""
	numpy = _numpy()
	array = numpy.asarray(values, dtype="<f4")
	norm = float(numpy.linalg.norm(array))
	if norm == 0.0:
		return True
	return abs(norm - 1.0) <= tolerance


class VectorBackend(Protocol):
	"""Two methods. The whole point is that there are only two."""

	def upsert(self, chunk_id: str, vector: Sequence[float]) -> None: ...

	def search(
		self,
		allowed_rooms: frozenset[str],
		query_vector: Sequence[float],
		limit: int,
	) -> list[Hit]: ...


class NumpyVectorBackend:
	"""In-process cosine over a candidate set the gate already filtered.

	``allowed_rooms`` is the **first positional parameter** of :meth:`search`, and it is
	re-applied here even though the loader has already applied it. That is not redundancy for
	its own sake: the loader is a callable supplied from outside this module, so "the caller
	filtered" is an assumption rather than a fact from this code's point of view, and the cost
	of checking is one set membership per row.
	"""

	def __init__(
		self,
		*,
		candidate_loader: CandidateLoader,
		writer: Callable[[str, str, int], None] | None = None,
		expected_dim: int | None = None,
	) -> None:
		self._load = candidate_loader
		self._write = writer
		self._expected_dim = expected_dim
		#: Rows skipped because their vector could not be read or did not match the current
		#: dimension. Surfaced rather than swallowed — a backend quietly ignoring half the
		#: corpus looks identical to a corpus with nothing relevant in it.
		self.skipped: list[str] = []

	def upsert(self, chunk_id: str, vector: Sequence[float]) -> None:
		if self._write is None:
			raise VectorBackendError("this backend was constructed read-only (no writer)")
		normalised = normalise(vector)
		self._write(chunk_id, encode_vector(normalised), int(normalised.shape[0]))

	def search(
		self,
		allowed_rooms: frozenset[str],
		query_vector: Sequence[float],
		limit: int,
	) -> list[Hit]:
		numpy = _numpy()
		self.skipped = []

		query = normalise(query_vector)
		if float(numpy.linalg.norm(query)) == 0.0:
			# No direction, so nothing is similar to it. Returning everything at score 0
			# would hand the ranker an arbitrary order and call it relevance.
			return []

		hits: list[Hit] = []
		for chunk, room, encoded, dim in self._load():
			if room not in allowed_rooms:
				continue
			try:
				vector = decode_vector(encoded, expected_dim=self._expected_dim or dim or None)
			except VectorBackendError as exc:
				self.skipped.append(f"{chunk}: {exc}")
				continue
			if vector.shape[0] != query.shape[0]:
				self.skipped.append(
					f"{chunk}: {vector.shape[0]} dimensions against a {query.shape[0]}-dimension query"
				)
				continue
			if not is_normalised(vector):
				# Stored un-normalised: score it correctly rather than trusting the write
				# path. The alternative is a silently wrong similarity, which is the exact
				# failure this module's docstring is about.
				vector = normalise(vector)
			hits.append(Hit(chunk=chunk, room=room, similarity=float(numpy.dot(query, vector))))

		hits.sort(key=lambda hit: (-hit.similarity, hit.chunk))
		return hits[: max(limit, 0)] if limit else hits


def _numpy() -> Any:
	"""Import ``numpy`` at call time, with a legible failure.

	Deferred rather than module-level so that the pure helpers in this package stay
	importable — and therefore testable — on a machine without it, and so that a missing
	``numpy`` produces a sentence rather than an ImportError from six frames down. It is
	already present in the production bench; this is about the test tier and about the
	message.
	"""
	try:
		import numpy
	except ImportError:  # pragma: no cover - numpy is installed in every real environment
		raise VectorBackendError(
			"numpy is required for chat vector scoring and is not importable. It is present "
			"in the production bench; a missing numpy here means the environment is not the "
			"bench. The semantic tier can be switched off in Chat Settings, which degrades "
			"retrieval to the lexical tier rather than failing every turn."
		) from None
	return numpy
