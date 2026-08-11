# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Triton Invocation Log — one row per ``@triton`` turn, and the only answer to "what does
this cost".

Instrumentation, not audit. The **audit** obligation is ``Chat Retrieval Audit``, which is
fail-closed: if that row cannot be written, retrieval does not return content. This table is
the opposite posture — it must never block a turn, so every write here is best-effort and a
failure is swallowed. Conflating the two would be a bug in either direction: an audit that
fails open is not an audit, and instrumentation that fails closed is an outage caused by a
metric.

``request_id`` is **derived from the triggering mention rather than generated**, and is
unique. Two consequences, both wanted: a redelivered interaction event produces one turn and
not two answers, and the whole cost of a turn is one row rather than a set nobody can join.

--------------------------------------------------------------------------------------
The columns that exist to answer a specific open question
--------------------------------------------------------------------------------------

* ``context_tokens`` — the ceiling was **chosen**, not derived from a model limit. The only
  honest way to retune it is two weeks of this column, which is why it is logged on every
  turn rather than sampled.
* ``cached_content_tokens`` — whether prompt-cache ordering is actually paying. A stable
  prefix that never engages the cache costs the full discount **silently**: no error, no log
  line, nothing but a larger bill.
* ``chunks_considered`` — candidates *after* the permission filter and *before* ranking. The
  vector backend's revisit trigger is written against this number rather than the corpus
  size, precisely because the filter runs first.
* ``citation_miss_count`` — citations the model emitted that resolved to nothing. Dropped
  silently in the answer, because a broken citation must not become a broken page, but
  counted here: a miss rate above a couple of percent is a **prompt** regression signal
  rather than a UI bug.
* ``queue_ms`` — the interaction webhook has a hard 30-second deadline, so a turn is
  acknowledged, enqueued and answered asynchronously. A growing value here is the first
  symptom of a backed-up worker, and the only one that appears before users notice.
* ``origin`` — recorded for observability **only**. The handler cannot see it: identical
  behaviour from either origin is guaranteed by the handler having no origin to branch on,
  not by a reviewer checking that it does not.

Zero DocPerm, like every chat DocType but the three documented exceptions, and in the MCP
denylist. It holds no message text — identifiers, counts and timings — but it does hold
*who asked Triton what, when*, which is a behavioural record of employees and belongs behind
the same door as the conversation itself.

Indentation is tabs.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

STATUS_RECEIVED: str = "Received"
STATUS_RETRIEVING: str = "Retrieving"
STATUS_GENERATING: str = "Generating"
STATUS_ANSWERED: str = "Answered"
STATUS_FAILED: str = "Failed"
STATUS_REFUSED: str = "Refused"

ORIGIN_GOOGLE_CHAT: str = "Google Chat"
ORIGIN_SPA: str = "SPA"

#: Terminal statuses. A turn in one of these is finished and must not be re-driven — unlike
#: the relay's job table, where a sweeper legitimately returns a stalled row to Pending.
TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_ANSWERED, STATUS_FAILED, STATUS_REFUSED})


class TritonInvocationLog(Document):
	def validate(self) -> None:
		if self.error:
			# Belt and braces against the one thing that must never reach this column.
			# The relay's own rule is that no message body reaches a log or an error field,
			# and an error string is the field people forget.
			self.error = str(self.error)[:2000]
