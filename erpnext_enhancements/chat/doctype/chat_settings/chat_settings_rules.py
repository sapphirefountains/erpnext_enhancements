# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Pure validation arithmetic for **Chat Settings** — no ``frappe``, no I/O, no imports.

This module deliberately imports nothing at all. The controller next door cannot be
imported without a Frappe runtime (``frappe.model.document``), and this repo has **no
Frappe integration-test job in CI** — so anything that needs a bench is verified by a
human or not at all. Everything here takes plain numbers and strings and returns plain
strings, which is what lets ``tests/test_chat_settings_budget.py`` run in the bench-free
tier where it will actually be executed on every push.

Nothing in this module raises and nothing here decides what an error *means*. Callers
decide: the controller turns a non-empty list into a single ``frappe.throw``.

**The T0 exclusion in :func:`validate_budgets` is the one non-obvious rule here, so it is
written out.** ADR §I.8 fixes the ceiling at 40,000 and the tier split at
T0 6,000 / T1 18,000 / T2 14,000 / T3 6,000 / reserve 2,000. Those five numbers sum to
**46,000**, which is 6,000 over the ceiling — but ``T1 + T2 + T3 + reserve`` is
``18,000 + 14,000 + 6,000 + 2,000 = 40,000``, the ceiling **exactly**. That is not a
coincidence: §I.8 also says *"unused budget flows T1 → T2 → T3"* and *"T0 neither borrows
nor lends, because its size must stay stable for the prompt cache"*. T0 is the stable
prompt-cacheable prefix (§I.9 segment S3); the ceiling governs the pool that actually
flows. Enforcing the naive "all five ≤ ceiling" reading would make the ADR's own defaults
invalid on the first save, which would fail ``bench migrate`` the moment
``default_chat_settings.py`` seeds the Single.

Because that reading is a judgement call rather than a quotation, it is exposed as
``include_t0_in_ceiling`` and defaults to the ADR-consistent reading. Flip it if the human
adjudicates the other way; nothing else has to change.
"""

# The ADR §I.8 defaults, restated here so a test can assert that the shipped defaults are
# a valid configuration. Kept in sync with chat_settings.json by
# tests/test_chat_settings_budget.py, which is the only thing that will notice a drift.
ADR_CONTEXT_TOKEN_CEILING: int = 40_000
ADR_BUDGET_T0_STABLE: int = 6_000
ADR_BUDGET_T1_THREAD: int = 18_000
ADR_BUDGET_T1_FLOOR: int = 4_000
ADR_BUDGET_T2_CROSS: int = 14_000
ADR_BUDGET_T3_AUTHORED: int = 6_000
ADR_BUDGET_RESERVE: int = 2_000

# Anything matching one of these in an identifier field is a credential somebody pasted
# into the wrong box. ADR §F.13's whole point is that Chat Settings holds no secret, and a
# Data field is exactly where a service-account JSON goes when a human is in a hurry.
_SECRET_MARKERS: tuple[str, ...] = (
	"-----begin",
	"private key",
	"private_key",
	'"type": "service_account"',
	'"type":"service_account"',
	"client_secret",
)


def validate_budgets(
	ceiling: float,
	t0: float,
	t1: float,
	t1_floor: float,
	t2: float,
	t3: float,
	reserve: float,
	*,
	include_t0_in_ceiling: bool = False,
) -> list[str]:
	"""Check the Triton context-tier budgets against the ceiling. Returns error strings.

	Positional order is ``ceiling, t0, t1, t1_floor, t2, t3, reserve`` and is fixed —
	``tests/test_chat_settings_budget.py`` calls it positionally.

	Args:
		ceiling: ``context_token_ceiling``. Must be positive.
		t0: ``budget_t0_stable`` — the prompt-cacheable prefix; neither borrows nor lends.
		t1: ``budget_t1_thread`` — the current thread's cap.
		t1_floor: ``budget_t1_floor`` — the guaranteed minimum T1 never degrades below.
		t2: ``budget_t2_cross`` — cross-room retrieval.
		t3: ``budget_t3_authored`` — the asking user's own authored history.
		reserve: ``budget_reserve`` — citation manifest and slack.
		include_t0_in_ceiling: count T0 against the ceiling too. Defaults to ``False``;
			see the module docstring for why the ADR's own numbers require that.

	Returns:
		A list of human-readable error strings, empty when the configuration is valid.
		The order is stable so a test can assert on it.
	"""
	errors: list[str] = []

	named: tuple[tuple[str, float], ...] = (
		("Context Token Ceiling", ceiling),
		("T0 Stable Budget", t0),
		("T1 Thread Budget", t1),
		("T1 Floor", t1_floor),
		("T2 Cross-Room Budget", t2),
		("T3 Authored Budget", t3),
		("Reserve", reserve),
	)

	# Negatives first: every rule below is arithmetic over these numbers, and a negative
	# would silently *satisfy* the sum check while breaking the assembler downstream.
	for label, value in named:
		if value < 0:
			errors.append(f"{label} cannot be negative (got {value}).")

	if ceiling <= 0:
		errors.append(f"Context Token Ceiling must be greater than zero (got {ceiling}).")
		# Every remaining rule compares against the ceiling, so there is nothing more
		# worth saying — reporting six consequential errors hides the one real one.
		return errors

	if t1_floor > t1:
		errors.append(
			f"T1 Floor ({t1_floor}) cannot exceed T1 Thread Budget ({t1}). "
			"The floor is the size T1 is guaranteed to keep when the ladder degrades; "
			"a floor above the budget means T1 can never be satisfied."
		)

	for label, value in named[1:]:
		if value > ceiling:
			errors.append(
				f"{label} ({value}) cannot exceed the Context Token Ceiling ({ceiling})."
			)

	pool = t1 + t2 + t3 + reserve
	if include_t0_in_ceiling:
		pool += t0
		pool_desc = "T0 + T1 + T2 + T3 + reserve"
	else:
		pool_desc = "T1 + T2 + T3 + reserve"

	if pool > ceiling:
		errors.append(
			f"The tier budgets plus reserve ({pool_desc} = {pool}) exceed the "
			f"Context Token Ceiling ({ceiling}). Lower a tier or raise the ceiling."
		)

	return errors


def validate_retention(
	message_retention_days: float,
	hard_delete_after_days: float,
	keep_tombstones_forever: bool,
	inbound_event_retention_days: float = 0,
	relay_job_retention_days: float = 0,
) -> list[str]:
	"""Check the retention dials for the one combination that means two things at once.

	``keep_tombstones_forever`` and ``hard_delete_after_days`` are two names for the same
	decision arriving from two documents — ADR §F.6.5 promotes soft deletes to real
	deletion after ``hard_delete_after_days`` (0 = never), and the Phase 1 prompt asks for
	a ``keep_tombstones_forever`` check that defaults on. Shipping both without a rule
	between them gives an operator two switches that disagree and no indication which one
	the purge job reads. The rule: ``keep_tombstones_forever`` wins, and setting a
	positive ``hard_delete_after_days`` while it is on is an error rather than a silently
	ignored setting.

	Returns:
		A list of human-readable error strings, empty when the configuration is valid.
	"""
	errors: list[str] = []

	named: tuple[tuple[str, float], ...] = (
		("Message Retention (days)", message_retention_days),
		("Hard Delete After (days)", hard_delete_after_days),
		("Inbound Event Retention (days)", inbound_event_retention_days),
		("Relay Job Retention (days)", relay_job_retention_days),
	)
	for label, value in named:
		if value < 0:
			errors.append(f"{label} cannot be negative (got {value}). Use 0 to keep forever.")

	if keep_tombstones_forever and hard_delete_after_days > 0:
		errors.append(
			"\"Keep Tombstones Forever\" is on but \"Hard Delete After (days)\" is set to "
			f"{hard_delete_after_days}. These contradict each other. Turn the tombstone "
			"guard off if you really want soft-deleted messages purged after a period, or "
			"set Hard Delete After (days) back to 0."
		)

	return errors


def validate_endpoint_url(url: str) -> list[str]:
	"""Check the JWT audience URL for the two mistakes that present as universal 401s.

	ADR §G.6 compares this value against the ``aud`` claim **byte for byte**, including
	the trailing slash, against whatever was typed into the Google Chat API console. A
	mismatch is not a validation error at the boundary — it is every inbound event
	silently rejected, and it reads like a signing problem, not a config problem. The two
	failures worth catching before that happens are surrounding whitespace from a paste
	and a non-HTTPS scheme.

	An empty value is allowed: the Single ships dormant and unconfigured, and refusing to
	save a half-filled settings page is worse than the thing it prevents.

	**Must be called before the caller trims the field.** ``interaction_endpoint_url`` is
	one of ``ChatSettings._TRIMMED_FIELDS``; if ``_trim_identifiers()`` has already run,
	``url != url.strip()`` can never be true and the whitespace branch below is dead code.
	``ChatSettings.validate()`` calls this against the raw value for exactly that reason.

	Returns:
		A list of human-readable error strings, empty when the value is acceptable.
	"""
	errors: list[str] = []

	if not url:
		return errors

	if url != url.strip():
		errors.append(
			"The Interaction Endpoint URL has leading or trailing whitespace. The JWT "
			"audience is compared byte for byte, so a stray space rejects every inbound "
			"event with a 401."
		)

	stripped = url.strip()
	if stripped and not stripped.startswith("https://"):
		errors.append(
			f"The Interaction Endpoint URL must start with 'https://' (got {stripped!r}). "
			"Google will not deliver Chat events to a plaintext endpoint."
		)

	return errors


def detect_secret_material(value: str) -> bool:
	"""True when a value looks like a credential rather than an identifier.

	Every field on Chat Settings is an identifier — a project id, a service-account
	*email*, a topic name, a URL. ADR §F.13 is emphatic that there is no ``Password``
	field here and that this is the entire point of the keyless design. The realistic
	failure is not a wrong design, it is a human pasting a service-account JSON into
	"Delegation Service Account" because that is where the credential feels like it goes.
	This catches the obvious shapes; it is a seatbelt, not a scanner. The scanner is
	``scripts/check_no_committed_secrets.py``.
	"""
	if not value:
		return False
	haystack = value.lower()
	return any(marker in haystack for marker in _SECRET_MARKERS)
