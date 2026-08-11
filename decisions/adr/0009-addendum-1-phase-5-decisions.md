# 0009 Addendum 1. What Phase 5 decided, and where the build diverged from the plan

**Status:** Accepted · **Date:** 2026-08-11 · **Amends:** [ADR 0009](0009-erpnext-google-chat-triton.md)
§I, and [Appendix B](0009-appendix-b-implementation-plan.md) §7

---

## Why this file exists

ADR 0009 is **immutable once accepted**, which is the repo's own rule and a good one: the
history of the reasoning survives because nobody edits it into agreement with what was
actually built. Appendix B §7 anticipated this addendum by name and required it — *"an ADR
addendum recording every decision this phase made that the ADR left open"*.

It carries three kinds of entry, and the distinction matters when you are reading it later:

* **§1 Decisions the ADR deferred to Phase 5**, now taken. These are new; the ADR says
  "Phase 5 decides" and this is Phase 5 deciding.
* **§2 Divergences from the ADR or Appendix B.** These *contradict* something written down.
  Each names what the plan said, what was built, and why — and the "why" is the point,
  because the recurring failure mode in this repository is somebody seeing a deliberate
  construct, assuming it is an oversight, and fixing it back.
* **§3 Corrections to the record.** Places where the ADR states something that turned out to
  be wrong, and one place where *this addendum's author* stated something wrong.

Everything here shipped in **v1.271.0 – v1.277.0**. Chat is still dormant
(`Chat Settings.enabled = 0`), so none of it changes behaviour on any site until it is
switched on.

---

## 1. Decisions the ADR deferred to Phase 5

### 1.1 The assembly split — what is pure and what is not

The ADR names `assemble.py` as pure and leaves the boundary vague. The boundary taken is
sharper than it had to be, and it is the reason most of this phase's judgement runs on every
push rather than nowhere:

| Module | Imports |
|---|---|
| `retrieval/rank.py`, `budget.py`, `assemble.py`, `lexical.py`, `indexing/chunker.py`, `invoke/envelope.py` | **stdlib only** |
| `retrieval/vectors.py`, `retrieval/citations.py` | stdlib + one deferred import |
| `retrieval/gate.py`, `indexing/*`, `invoke/*` | Frappe |

This repo has **no Frappe integration-test job**, so anything needing a bench is verified by a
human or not at all. Putting the ranking, the ladder, the assembly order, the query builder
and the chunk boundaries in import-free modules moves them into the tier CI runs, and leaves
only *fetching* behind the bench. 81 assertions run on every push as a result.

Two consequences worth naming:

* **Every clock read lives in `gate.py` or `indexer.py`**, never in a pure module. `rank`
  takes an *age in days*; `chunker` takes *minutes since the previous message*; `assemble`
  takes no time at all. A `now()` inside a pure function makes two identical assemblies
  differ, which silently costs the whole prompt-cache discount.
* **`chat/retrieval/__init__.py` re-exports lazily**, through a module `__getattr__`. An eager
  `from .gate import retrieve` would run `import frappe` on any touch of the package —
  including `budget`, which exists precisely so its arithmetic can be tested without a bench.
  One eager import would have moved five pure modules out of the tier CI runs.

### 1.2 Vector storage — and the consequence of the adapter's shape

Built as `DECISIONS.md` D4 specifies: base64 of raw `float32` bytes in a `Long Text` column,
scored with in-process numpy cosine over the **permission-filtered** candidate set, behind a
two-method adapter.

**The consequence the ADR does not state, and which someone will hit:** the adapter takes a
`candidate_loader` callable rather than running its own query, because the invariant outranks
the signature — all chat SQL lives in the gate, and a backend running its own `SELECT` would
be a second place the permission filter could be forgotten. So a future MariaDB-native vector
backend **cannot simply be dropped in behind this interface**; its query belongs in the gate
too, or the one-door rule gets revisited on purpose. The adapter still buys what it was for:
the *scoring* implementation is one file.

`normalise()` is applied on the way in **and asserted on the way out**, at both ends, because
a truncated (Matryoshka) vector is not unit length and cosine over non-unit vectors is a
different function that returns plausible numbers.

### 1.3 The MCP identity mechanism

The denylist sits in `assistant_tools/_gate.py`'s existing `_safe_execute` wrap, as the ADR
§I.2.2 recommends — but the **placement within that function** is the decision, and it is not
optional: the branch is *first*, above the confirm-flow bypass and above the
`ai_write_gating_enabled` check.

That flag ships dormant. A denylist below it would be **off in production today**, which is
the shipped state. Both orderings refuse while gating is on; only one refuses while it is off.
Asserted on the source, because a passing call cannot reveal it.

Two surfaces, two mechanisms: a `doctype` argument is a string comparison applied to *every*
tool (so a tool added to FAC tomorrow is covered the day it appears); free text
(`run_database_query`, `run_python_code`) is matched by contact after case-folding, comment
stripping and dropping every non-word character. **No attempt is made to allow "safe"
queries.** Over-refusal costs an analyst one rephrase; under-refusal costs the invariant.

### 1.4 Token counting (R03-V12)

`ceil(len/4) × 1.15`, recorded per chunk in `token_count_method`. The heuristic **over**-
estimates deliberately: erring high under-fills the ceiling, erring low overflows it, and an
overflow is discovered by the model provider rather than by us. The field exists so a later
correction pass can find the estimates instead of guessing which rows to trust.

### 1.5 DM threading

Google cannot represent threads at all, so the structure is ERPNext's alone. In a DM — or any
room where the mention is not in a thread — the T1 tier falls back to the **room's tail**
rather than to nothing. `_thread_messages` omits the thread predicate when `thread_root` is
`None`; the reply still lands in the room, and there is no thread for it to land in.

### 1.6 The four CQs answered by building, each reversibly

| CQ | Answer built | How to reverse it |
|---|---|---|
| **CQ-17** ceiling | 40,000, as `context_token_ceiling`, with the realised count logged on every turn | change the field; retune from two weeks of `Triton Invocation Log.context_tokens` |
| **CQ-18** reply exemption | the narrow exemption, as `triton_reply_exempt_from_confirmation` (ships **on**) | untick it — which **disables `@triton`**, it does not gate the reply. Declining the exemption means building an approval surface inside Google Chat, which is a design task with a schedule |
| **CQ-23** vectors | base64 + numpy behind the adapter | one file, plus §1.2's caveat |
| **CQ-24** bridge | reuse the existing `erpnext-bridge` token exchange | ~150 lines in the Triton repo |

**CQ-24's cost, stated rather than glossed:** the endpoint and its secret keep saying
"erpnext" while serving Chat, and that same secret is the telephony gateway's — so its blast
radius is wider than its name suggests. The security posture is identical either way: it is a
token-exchange grant authorising the *impersonation request*, not a superuser session.

---

## 2. Divergences from the ADR and Appendix B

### 2.1 T5-3 could not be implemented as written

**Appendix B says:** *"every SQL literal under `erpnext_enhancements/chat/**` lives in
`gate.py` and contains `allowed_rooms`"*.

**That sentence predates Phase 3 and is now false by construction.** `api/history.py` pages
the transcript with keyset SQL, `api/search.py` runs the lexical search, `permissions.py` *is*
the membership fragment, and `health.py` reads aggregates under `bench execute`. All four are
legitimate and all four are already policed, per `(file, function, table)` triple with a
written justification each, by `tests/test_chat_rawsql_guard.py`.

Enforcing the sentence literally left two options, both worse: delete that guard, or exempt
the whole chat package from this one. **What is enforced is the half carrying the security
weight** — the *retrieval path* has one door — plus three rules the appendix implies but does
not state: `allowed_rooms` first-positional on every builder, exactly two exported symbols,
and no parameter by which a caller supplies room ids.

### 2.2 The dirty predicate is derived, not counted

**ADR §I.6 specifies** `unsummarized_count >= 25 OR digest_dirty_since < now() - 15 minutes`,
which implies two counter columns maintained on the message write path.

**Built as a derivation** from rows that already exist: `Chat Room.seq_high_water` minus the
digest's `watermark_seq` *is* the unsummarised count, and `last_message_at` against
`generated_at` *is* the dirty age.

A counter is a second source of truth for a number already known, it costs a write on the
hottest path in the feature, and the copy that drifts is the one nobody notices. Derived is
self-correcting — delete a digest and the next pass rebuilds it — and `coalesce(..., 0)` makes
a room with no digest dirty *by definition*, so the existing history needs no backfill script.
The same reasoning gives the chunk indexer no cursor column.

### 2.3 There is no second world-reachable endpoint

**Appendix B specifies** `chat/invoke/webhook.py`: a new `allow_guest` endpoint with its own
JWT verification.

**Not built.** `chat/gchat/webhook.py` already *is* that endpoint — it verifies signature,
issuer, byte-exact audience and expiry before any body parsing and before any database access,
and **its path is baked into the audience Google mints tokens against**. A second endpoint
means a second copy of an authentication boundary, and the copy that drifts is a
world-reachable open relay. The existing endpoint gained a dispatch call.

### 2.4 The two normalisers are one file

**Appendix B specifies** `normalize_gchat.py` and `normalize_spa.py`.

**Built as `normalize.py`,** because the property they exist to guarantee is that the two
produce byte-identical envelopes for the same logical mention. Side by side, a field one sets
and the other forgets is visible on one screen. In two files it is visible to a test and to
nobody else — and the test is the thing most likely to be written to match whichever
implementation was finished first.

### 2.5 The indexing package may name the index tables

The one-door rule is about **reads for a person**. Something has to *write* the index, and it
cannot be the gate: the gate answers one person from their own rooms, while the indexer and
the summariser read every room and belong to nobody.

So `chat/indexing/**` is exempted from both source guards **as a package**, and pays four
prices, each asserted in both guard files so deleting one does not silently remove the other's
precondition:

1. **no `@frappe.whitelist()` anywhere in it** — no HTTP request can ask it for anything;
2. every public function named and justified individually;
3. every one of those a registered scheduler job, bar the staleness seam's writer;
4. nothing under `chat/api/` imports it.

Exempted as a package rather than per triple because every function makes the same argument,
and twenty copies of one argument is twenty entries nobody re-reads — which is worse for a
security list than one entry somebody does.

### 2.6 Smaller divergences, each with its reason

| Built | Instead of | Why |
|---|---|---|
| dispatch called from `compose.send_message` | a `doc_events` hook | a hook runs inside the inserting transaction on a web worker, and a hook is where the next person adds the call that reaches Google — at which point a timeout becomes a *failed message insert*. Invariant I1 |
| dedupe on the `Triton Invocation Log` row | `frappe.enqueue(deduplicate=True)` | that flag drops a new enqueue while an existing job is `QUEUED` **or `STARTED`** — the same trap that makes the digest a batch |
| `poisoned` separate from `is_stale` | one staleness flag | "nobody has rebuilt this yet" and "this cannot be rebuilt" need different answers from an operator |
| thread digests generated in the same pass | a separate job, or not at all | the gate reads that table; leaving it unwritten would be a feature that reads as "this never happens" rather than "nobody built the other half" |
| no `snippet` in the citation manifest | the client's full shape | a snippet is message text, and the manifest is stored on `Triton Invocation Log`, which is content-free by construction |
| `_acting_as` sets the session user for the duration of a retrieval | passing the user to every helper | necessary and not sufficient: `membership_filter_sql` resolves its own default from the session, and a background job's session is `Administrator`, for which it returns `1 = 1` |
| four new columns added to the audit's **signed** set | stored unsigned | an audit row whose *scale* can be edited without breaking the chain is one where "Triton read four messages" quietly becomes the record of a read of four hundred |

---

## 3. Corrections to the record

### 3.1 An identifier does not survive as one search term

**ADR §I.5's T-4** is written as *"an exact invoice number outranks a topically similar chunk
lacking it"*, and the natural implementation reads as "preserve `SINV-04412` as a single
term". **That would match nothing at all.** InnoDB's tokenizer splits the stored body on
non-word characters too, so the index holds `sinv` and `04412` separately.

The mechanism that makes T-4 true is a **required conjunction of both parts**. Every term is
`+`-required, so the search is an AND — which is also why the boolean-mode builder strips
operator characters rather than escaping them, and why terms below
`innodb_ft_min_token_size` are dropped from the required set and *reported* rather than
silently ignored.

Caught by a test that asserted the wrong thing first.

### 3.2 CQ-14 was not open, and this author said it was

v1.272.0 and v1.273.0 shipped a `manifest_backed_source_chips` setting defaulting to **off**,
described as preserving today's chip row while CQ-14 remained open.

**CQ-14 was answered on 2026-08-10.** The sources row shows the full retrieved manifest with
cited entries marked and sorted first, uncited ones dimmed rather than dropped; the approval is
recorded in `CHANGELOG.md` v1.265.0 and the reasoning is in `public/js/chat/citations.js`'s
own docstring. Phase 3 built it.

So the switch defaulted to *disabling an approved, shipped behaviour* — the lying-settings-
field trap v1.270.0 removed from the presence constants. The field was deleted in v1.276.0.
**Four CQs remain open, not five.**

### 3.3 The Python manifest was keyed on the wrong field

`Citation.as_dict` first emitted `ref`/`kind`. The renderer that shipped in Phase 3 indexes on
`entry.k` and switches on `citation.type`, and `indexManifest` **silently discards** any entry
whose `k` is not a positive integer.

Nothing would have raised. Every citation would have been a miss, every `[[ref:N]]` marker
stripped from the answer, and the reader would have seen prose with no numbers in it and no
sign there were meant to be any. Fixed in v1.276.0; the keys are now asserted against
`citations.js`'s own source rather than against a copy of the contract.

---

## 4. `VERIFY:` items still open after this phase

| Item | Why it still matters |
|---|---|
| **Does `bench migrate` drop a hand-added FULLTEXT index?** | If it does, the exact-match half of retrieval degrades **invisibly** after every deploy. The `after_migrate` backstop makes the bad answer a one-migrate window rather than permanent, so this is now "is the backstop load-bearing or belt-and-braces" — but the answer belongs in the record. Procedure in `tests/test_chat_triton_bench.py::test_record_here_whether_migrate_drops_it` |
| **Does the prompt cache actually engage?** | `Triton Invocation Log.cached_content_tokens` is logged for exactly this. A stable prefix that never engages costs the full discount **silently** |
| **Is the 40,000-token ceiling right?** | `context_tokens` is logged per turn. Retune from data, not from argument |
| **Does `{FRAPPE_BASE_URL}/desk/{slug}/{name}` resolve on v16?** | Inherited from the ADR. If not, every ERPNext citation chip is already a dead link today and inline citations inherit the bug |
| **Does the instance carry the `cloud-platform` scope?** | `auth.get_vm_access_token` relies on the VM's own configuration. Wrong scope is a 403 from Vertex AI, and the semantic tier degrades to the lexical one — correctly, but silently |

---

## 5. What Phase 6 may assume

* All retrieval SQL is in one file, and two source-level tests fail the build if that stops
  being true.
* Every non-participant read writes a signed, fail-closed audit row **before** returning
  content, and the four scale columns on it are populated.
* The chat DocTypes are unreadable through every generic AI tool, by every role, with the AI
  write gate on or off.
* `@triton` answers from both origins through one handler that cannot see which one asked.
* The index maintains itself on the scheduler, and an edit or delete invalidates every chunk
  and digest overlapping the changed span, synchronously, on the request that made the change.
* **Nothing has run against real data.** Every claim above is asserted by tests CI runs, by
  tests CI does not run, or by a source scan — and the live round trip, the bench suite and
  the evaluation baseline are all still outstanding at the time of writing.
