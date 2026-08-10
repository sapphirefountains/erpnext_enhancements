"""Bench-free tests for the relay-job state machine.

Subject: ``erpnext_enhancements.chat.sync.states``, which imports only the standard
library. That is what makes this suite possible at all — this repo has no Frappe
integration-test job (``CLAUDE.md``), so anything requiring a bench is verified by a human
or not verified. The state machine is the highest-leverage thing in Phase 2 to hold still,
because every status write in the sync engine goes through :func:`assert_transition`.

Three properties are worth more than the rest and each has a test named after it:

**The enum is the shipped DocType, not the proposal.** The Phase 2 brief proposes
``Queued / Sending / Synced / Retrying / Cancelled``; the repo shipped
``Pending / In Progress / Done / Failed / Dead / Skipped``. The enum is asserted against
``chat_relay_job.json`` so the two cannot drift, and renaming a Select option needs a data
patch — the drift would not merely be untidy, it would make existing rows unsaveable.

**The transition table is enumerated as a full cross product.** Every ordered pair of
states is either explicitly legal or must raise. A state added to the DocType without an
entry in ``LEGAL_TRANSITIONS`` therefore fails here rather than at 3am, and — because the
expected edge set is written out again below — so does an edge quietly added to the table.

**The scheduling delay is not the HTTP backoff.** ``next_attempt_delay`` must stay
deterministic while ``gchat.backoff.compute_backoff`` stays jittered; conflating them makes
the sweeper untestable. That contrast is pinned by an actual comparison, not a comment.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_sync_states.py -q`` step in CI.
``python -m unittest`` silently collects zero tests from a file shaped like this and
reports success; this repo lost a suite that way for weeks.
"""

from __future__ import annotations

import ast
import itertools
import json
import pathlib
import re

import pytest

from erpnext_enhancements.chat.sync import states
from erpnext_enhancements.chat.sync.states import (
	LEGAL_TRANSITIONS,
	RETRYABLE_STATES,
	TERMINAL_STATES,
	IllegalTransition,
	MessageSyncState,
	RelayState,
)

APP_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]
STATES_PY: pathlib.Path = APP_DIR / "chat" / "sync" / "states.py"
RELAY_JOB_JSON: pathlib.Path = APP_DIR / "chat" / "doctype" / "chat_relay_job" / "chat_relay_job.json"
RELAY_JOB_PY: pathlib.Path = APP_DIR / "chat" / "doctype" / "chat_relay_job" / "chat_relay_job.py"
CHAT_MESSAGE_JSON: pathlib.Path = APP_DIR / "chat" / "doctype" / "chat_message" / "chat_message.json"

#: A second, independent copy of the transition table. Written out longhand on purpose: if
#: this file imported the table and derived expectations from it, every assertion below
#: would be a tautology and an edge added by hand would sail through. Keeping two copies
#: means a deliberate change costs two edits and an accidental one costs a red build.
EXPECTED_EDGES: frozenset[tuple[str, str]] = frozenset(
	{
		("Pending", "Pending"),
		("Pending", "In Progress"),
		("Pending", "Skipped"),
		("Pending", "Dead"),
		("In Progress", "Done"),
		("In Progress", "Failed"),
		("In Progress", "Pending"),
		("In Progress", "Skipped"),
		("In Progress", "Dead"),
		("Failed", "Pending"),
		("Failed", "Dead"),
		("Failed", "Skipped"),
	}
)

#: ``Chat Message.sync_origin`` values, from that DocType's Select options.
ORIGINS: tuple[str, ...] = ("ERPNext", "Google Chat", "Triton")

#: Every ``Chat Relay Job.operation``, message-bearing or not.
OPERATIONS: tuple[str, ...] = (
	"Message Create",
	"Message Update",
	"Message Delete",
	"Space Create",
	"Space Update",
	"Member Add",
	"Member Remove",
	"Attachment Upload",
)


def _select_options(path: pathlib.Path, fieldname: str) -> list[str]:
	"""The ``Select`` options of one field, in declaration order."""
	data = json.loads(path.read_text(encoding="utf-8"))
	for field in data.get("fields") or []:
		if field.get("fieldname") == fieldname:
			return [line for line in str(field.get("options") or "").split("\n") if line]
	raise AssertionError(f"{path.name} declares no field {fieldname!r}")


# --- the module stays in the bench-free tier -----------------------------------


def test_states_imports_nothing_outside_the_standard_library() -> None:
	"""The property that keeps this suite runnable.

	``frappe`` and ``requests`` are not installed on the bench-free runner. The moment one
	of them appears here — for a ``_()`` on an error message, most likely — this file stops
	being collectable and the transition table goes back to being unverified. Translate in
	the caller, not in the state machine.
	"""
	tree = ast.parse(STATES_PY.read_text(encoding="utf-8"), filename=str(STATES_PY))
	roots = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			roots.update(alias.name.split(".")[0] for alias in node.names)
		elif isinstance(node, ast.ImportFrom) and node.level == 0:
			roots.add((node.module or "").split(".")[0])
	assert roots <= {"__future__", "collections", "enum", "typing"}, (
		f"{STATES_PY.name} imports {sorted(roots)}; it must stay standard-library only so the "
		"bench-free CI tier can import it (see this file's docstring)"
	)


# --- the enums are the shipped DocTypes ----------------------------------------


def test_relay_state_matches_the_shipped_select_options() -> None:
	"""``RelayState`` is a transcription of ``Chat Relay Job.status``, order included.

	Order matters because the Select renders in it, and value equality matters because the
	enum values are written straight into the column. A mismatch here is either a rename
	that needs a data patch or an enum that will write a value the field rejects.
	"""
	assert [state.value for state in RelayState] == _select_options(RELAY_JOB_JSON, "status")


def test_there_is_no_retrying_state() -> None:
	"""Stated as its own test because it is a design decision, not an omission.

	A transient failure returns to ``Pending`` with ``available_at`` pushed forward and
	``attempts`` incremented; ``attempts > 0`` distinguishes "never tried" from "retrying".
	One fewer state to get wrong, and the sweeper's query is the same either way. If a
	``Retrying`` option is ever added to the DocType, this fails before the code does.
	"""
	values = {state.value for state in RelayState}
	assert "Retrying" not in values
	assert values == {"Pending", "In Progress", "Done", "Failed", "Dead", "Skipped"}


def test_message_sync_state_matches_the_shipped_select_options() -> None:
	assert [state.value for state in MessageSyncState] == _select_options(CHAT_MESSAGE_JSON, "sync_state")


def test_message_operations_are_real_relay_operations() -> None:
	"""``MESSAGE_OPERATIONS`` is a hand-copy (the controller imports ``frappe``), so pin it.

	A typo here would not raise anywhere: :func:`project_to_message_state` would simply
	return ``None`` forever and every relayed message would stay ``Pending`` in the SPA.
	"""
	declared = set(_select_options(RELAY_JOB_JSON, "operation"))
	assert set(OPERATIONS) == declared, "this file's OPERATIONS list has drifted from the DocType"
	assert states.MESSAGE_OPERATIONS <= declared, (
		f"states.MESSAGE_OPERATIONS contains values the DocType does not declare: "
		f"{sorted(states.MESSAGE_OPERATIONS - declared)}"
	)
	assert states.MESSAGE_OPERATIONS == {"Message Create", "Message Update", "Message Delete"}


# --- the table itself -----------------------------------------------------------


def test_every_state_has_an_entry_and_no_entry_is_invented() -> None:
	"""Totality. A missing key would raise ``KeyError`` deep inside a relay worker instead
	of failing here, and the traceback would name a dict lookup rather than the real cause."""
	assert set(LEGAL_TRANSITIONS) == set(RelayState)
	for source, targets in LEGAL_TRANSITIONS.items():
		assert set(targets) <= set(RelayState), f"{source} lists a non-state successor"


def test_the_edge_set_is_exactly_the_documented_one() -> None:
	"""The two copies of the table agree. See ``EXPECTED_EDGES``."""
	actual = frozenset(
		(source.value, target.value) for source, targets in LEGAL_TRANSITIONS.items() for target in targets
	)
	assert actual == EXPECTED_EDGES, (
		"the transition table changed. Added: "
		f"{sorted(actual - EXPECTED_EDGES)}; removed: {sorted(EXPECTED_EDGES - actual)}. "
		"If the change is deliberate, update EXPECTED_EDGES *and* the docstring on "
		"LEGAL_TRANSITIONS that explains each edge — an undocumented edge is how a state "
		"machine rots."
	)


def test_every_legal_transition_is_accepted() -> None:
	"""Half of the cross product, enumerated rather than sampled."""
	for source, targets in LEGAL_TRANSITIONS.items():
		for target in targets:
			assert states.assert_transition(source, target) is target


def test_every_illegal_transition_raises() -> None:
	"""The other half. Enumerated programmatically so a new state cannot slip in untested.

	This is the assertion that makes the machine a machine: it is not enough that the legal
	moves work, the illegal ones must be *refused*, or the table is documentation rather
	than a gate.
	"""
	for source, target in itertools.product(RelayState, RelayState):
		if target in LEGAL_TRANSITIONS[source]:
			continue
		with pytest.raises(IllegalTransition):
			states.assert_transition(source, target)


def test_the_cross_product_is_not_vacuous() -> None:
	"""Prove the enumeration above actually covers both branches.

	Six states is 36 ordered pairs, of which 12 are legal. If either count ever reads zero
	the two tests above would pass by iterating over nothing — the failure mode that made
	this repo's earlier lost test suite invisible.
	"""
	pairs = list(itertools.product(RelayState, RelayState))
	legal = [(a, b) for a, b in pairs if b in LEGAL_TRANSITIONS[a]]
	assert len(pairs) == 36
	assert len(legal) == 12, f"expected 12 legal edges, found {len(legal)}"


def test_terminal_states_have_no_successors() -> None:
	"""Terminal means terminal: ``Done``, ``Dead`` and ``Skipped`` go nowhere.

	Resurrecting a ``Dead`` row would violate Rule 1 (CREATE-BEFORE-EDIT): its ``job_seq``
	is the per-room FIFO position, later jobs for that room have already drained past it,
	and replaying a stale position can apply an edit before the create it edits. A manual
	re-send is a **new** job with a new ``job_seq``.
	"""
	assert TERMINAL_STATES == {RelayState.DONE, RelayState.DEAD, RelayState.SKIPPED}
	for state in TERMINAL_STATES:
		assert LEGAL_TRANSITIONS[state] == frozenset(), f"{state.value} is not terminal any more"


def test_terminal_and_retryable_do_not_overlap_and_cover_the_machine() -> None:
	assert RETRYABLE_STATES == {RelayState.PENDING, RelayState.IN_PROGRESS}
	assert not (TERMINAL_STATES & RETRYABLE_STATES)
	# ``Failed`` is in neither: it is a decision point, not a queue entry.
	assert set(RelayState) - TERMINAL_STATES - RETRYABLE_STATES == {RelayState.FAILED}


def test_in_progress_can_only_be_entered_from_pending() -> None:
	"""One claim edge, so the in-flight set is unambiguous.

	The set of in-flight writes for a room is ``status = 'In Progress' and lease_expires_at
	> now()``, and that same set is the crashed-worker detector. A second way into
	``In Progress`` — a "retry now" button that claims a ``Failed`` row directly, say —
	would make both readings ambiguous at once.
	"""
	entrances = [s.value for s, t in LEGAL_TRANSITIONS.items() if RelayState.IN_PROGRESS in t]
	assert entrances == ["Pending"], f"In Progress is reachable from {entrances}"


def test_the_purgeable_statuses_on_the_controller_are_exactly_the_terminal_ones() -> None:
	"""Retention may only delete rows no transition can leave.

	``chat_relay_job.py`` imports ``frappe``, so it cannot be imported here; the constant is
	read out of the source instead. If retention ever gained a non-terminal status, a
	daily maintenance run would delete a message a coworker believes they sent.
	"""
	source = RELAY_JOB_PY.read_text(encoding="utf-8")
	literals = dict(re.findall(r'(STATUS_[A-Z_]+): Final\[str\] = "([^"]+)"', source))
	block = re.search(r"PURGEABLE_STATUSES[^=]*=\s*\(([^)]*)\)", source)
	assert block, "chat_relay_job.py no longer declares PURGEABLE_STATUSES as a tuple literal"
	purgeable = {literals[name] for name in re.findall(r"STATUS_[A-Z_]+", block.group(1))}
	assert purgeable == {state.value for state in TERMINAL_STATES}, (
		f"retention purges {sorted(purgeable)} but the terminal states are "
		f"{sorted(s.value for s in TERMINAL_STATES)}"
	)


# --- assert_transition's own behaviour -----------------------------------------


def test_transitions_accept_the_stored_strings() -> None:
	"""Callers hold ``job.status``, which is a ``str`` off the database, not an enum."""
	assert states.assert_transition("Pending", "In Progress") is RelayState.IN_PROGRESS
	assert states.assert_transition(RelayState.PENDING, "Skipped") is RelayState.SKIPPED
	assert states.assert_transition("In Progress", RelayState.DONE) is RelayState.DONE
	# Mixed forms behave identically, and an illegal move is illegal in every form: a job
	# cannot reach ``Done`` without having been claimed, because nothing called Google.
	with pytest.raises(IllegalTransition):
		states.assert_transition("Pending", "Done")


def test_an_unknown_status_raises_rather_than_denying_quietly() -> None:
	"""A status nobody declared is the same class of bug as an illegal move, and both must
	stop the write. Returning ``False`` would let a typo park a job in limbo."""
	with pytest.raises(IllegalTransition) as caught:
		states.assert_transition("Queued", "In Progress")
	assert "current" in str(caught.value), str(caught.value)

	with pytest.raises(IllegalTransition) as caught:
		states.assert_transition("Pending", "Synced")
	assert "target" in str(caught.value), str(caught.value)


def test_the_refusal_says_what_would_have_been_allowed() -> None:
	"""An error an operator cannot act on is an error that gets worked around."""
	with pytest.raises(IllegalTransition) as caught:
		states.assert_transition(RelayState.DONE, RelayState.PENDING)
	message = str(caught.value)
	assert "terminal" in message, message

	with pytest.raises(IllegalTransition) as caught:
		states.assert_transition(RelayState.FAILED, RelayState.IN_PROGRESS)
	assert "Pending" in str(caught.value), str(caught.value)


def test_the_gate_returns_the_target_so_it_cannot_be_forgotten() -> None:
	"""``job.status = assert_transition(job.status, target)`` — skipping the gate writes
	nothing, which is the point of returning a value instead of validating in place."""
	assert states.assert_transition("In Progress", "Done") == "Done"
	assert isinstance(states.assert_transition("In Progress", "Done"), RelayState)


# --- the projection onto Chat Message ------------------------------------------


def test_the_projection_is_total_over_every_combination() -> None:
	"""State × operation × origin, all 144 of them, return a ``MessageSyncState`` or ``None``.

	Totality is the contract: the caller writes the answer or writes nothing, and there is
	no third branch to forget.
	"""
	for state, operation, origin in itertools.product(RelayState, OPERATIONS, ORIGINS):
		result = states.project_to_message_state(state, operation=operation, origin=origin)
		assert result is None or isinstance(
			result, MessageSyncState
		), f"{state}/{operation}/{origin} produced {result!r}"


def test_an_inbound_row_is_inbound_forever() -> None:
	"""``sync_origin = "Google Chat"`` never projects, whatever the relay job does.

	An outbound edit of an inbound message does not make the message ours, and overwriting
	``Inbound`` with ``Relayed`` would lose the only field that says which side authored it.
	"""
	for state, operation in itertools.product(RelayState, OPERATIONS):
		assert states.project_to_message_state(state, operation=operation, origin="Google Chat") is None


def test_non_message_operations_never_project() -> None:
	"""A membership or space job has no message to project onto. Projecting one would mark
	an unrelated message failed because somebody could not be added to a room."""
	for state, operation in itertools.product(RelayState, OPERATIONS):
		if operation in states.MESSAGE_OPERATIONS:
			continue
		assert states.project_to_message_state(state, operation=operation, origin="ERPNext") is None


def test_the_projection_for_an_outbound_message() -> None:
	"""The mapping a human sees, pinned value by value."""
	expected = {
		RelayState.PENDING: MessageSyncState.PENDING,
		RelayState.IN_PROGRESS: MessageSyncState.PENDING,
		RelayState.DONE: MessageSyncState.RELAYED,
		RelayState.FAILED: None,
		RelayState.DEAD: MessageSyncState.FAILED,
		RelayState.SKIPPED: MessageSyncState.NOT_MIRRORED,
	}
	for state, want in expected.items():
		got = states.project_to_message_state(state, operation="Message Create", origin="ERPNext")
		assert got is want, f"{state.value} projected to {got!r}, expected {want!r}"


def test_a_transient_failure_does_not_flash_the_message_red() -> None:
	"""``Failed -> None`` and ``Dead -> Failed``, stated as the decision it is.

	A ``Failed`` row is on its way back to ``Pending`` with a pushed ``available_at``.
	Painting the coworker's message red on every 503 and green again a second later trains
	people to ignore the colour, and then nobody notices the ``Dead`` one that mattered.
	"""
	assert states.project_to_message_state("Failed", operation="Message Create", origin="ERPNext") is None
	assert (
		states.project_to_message_state("Dead", operation="Message Create", origin="ERPNext")
		is MessageSyncState.FAILED
	)


def test_triton_authored_messages_project_like_erpnext_ones() -> None:
	"""Triton's replies are relayed outward, so they carry relay progress. Three-valued
	origin, not a boolean, precisely so this row is distinguishable — but not silent."""
	assert (
		states.project_to_message_state("Done", operation="Message Create", origin="Triton")
		is MessageSyncState.RELAYED
	)


def test_an_unexpected_origin_still_reports_relay_progress() -> None:
	"""Defensive direction. An origin nobody declared is most likely a row written before a
	custom field existed (``doc_events`` fire during ERPNext's test bootstrap); reporting
	progress is recoverable, going permanently silent is not."""
	assert (
		states.project_to_message_state("Done", operation="Message Create", origin="")
		is MessageSyncState.RELAYED
	)


# --- next_attempt_delay ---------------------------------------------------------


def test_the_first_retry_waits_exactly_the_base() -> None:
	"""``attempts`` is the count **after** the failing attempt is recorded, so 1 means
	"one attempt has been made". An operator setting ``relay_initial_backoff_seconds = 2``
	means "the first retry is two seconds later", and this is where that promise lives."""
	assert states.next_attempt_delay(1, 2.0, 32.0) == 2.0


def test_the_delay_doubles_and_then_saturates() -> None:
	sequence = [states.next_attempt_delay(n, 2.0, 32.0) for n in range(1, 8)]
	assert sequence == [2.0, 4.0, 8.0, 16.0, 32.0, 32.0, 32.0]


def test_the_delay_is_deterministic() -> None:
	"""No jitter, called a hundred times. This is the property the sweeper's tests rest on:
	"a failure at T becomes available at T+2s" is only assertable if the answer is a
	number rather than a window."""
	assert len({states.next_attempt_delay(3, 0.5, 32.0) for _ in range(100)}) == 1


def test_next_attempt_delay_is_not_compute_backoff() -> None:
	"""The two functions answer different questions and must not be merged.

	``compute_backoff`` is the **jittered sleep inside one HTTP call**; this is the
	**scheduling delay between job attempts**. Proven rather than asserted in a comment: a
	hundred draws from the jittered one produce many distinct values, and every draw stays
	inside its own cap.
	"""
	from erpnext_enhancements.chat.gchat import backoff

	draws = {backoff.compute_backoff(3, 0.5, 32.0) for _ in range(100)}
	assert len(draws) > 1, "compute_backoff has lost its jitter; the two functions have merged"
	assert max(draws) <= 32.0
	assert len({states.next_attempt_delay(3, 0.5, 32.0) for _ in range(100)}) == 1


def test_zero_and_negative_attempts_are_clamped_not_rejected() -> None:
	"""Called on the error path. Raising inside the handler that records an error is how a
	job dies with an empty Error Log — this repo has that bug documented already."""
	assert states.next_attempt_delay(0, 2.0, 32.0) == 2.0
	assert states.next_attempt_delay(-5, 2.0, 32.0) == 2.0


def test_a_huge_attempt_count_does_not_overflow() -> None:
	"""``2 ** attempts`` with an unbounded ``attempts`` is an ``OverflowError`` waiting for a
	corrupt row."""
	assert states.next_attempt_delay(10**6, 2.0, 32.0) == 32.0


def test_the_delay_is_never_negative_and_never_exceeds_the_cap() -> None:
	for attempts in range(0, 40):
		for base, cap in ((0.5, 32.0), (2.0, 32.0), (0.0, 32.0), (5.0, 1.0), (-1.0, -1.0)):
			delay = states.next_attempt_delay(attempts, base, cap)
			assert delay >= 0.0, (attempts, base, cap, delay)
			assert delay <= max(cap, 0.0), (attempts, base, cap, delay)
