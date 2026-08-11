"""Bench-free structural guards on the Chat module — the fence, shipped before the field.

Every assertion here defends a decision that is **effectively unfixable once data lands**:
a naming rule you cannot change without renaming every row, a `varchar(140)` that has
already truncate-collided two Google messages into one, a `permissions` array somebody
widened so they could look at a message in the desk. None of those fail loudly at runtime.
They fail as silence — a message that never arrives, a body that leaked into a table with
no retention rule, a room list an MCP session could read.

So the failure *messages* below are the documentation. A future contributor meets this
suite exactly once, at the moment they break it, and whatever the message says is the only
explanation they will get. Write them accordingly.

Filesystem + `ast` + `json` only. No bench, no `frappe` import, no stub — which is why
this suite runs on every push instead of on the days somebody remembers to run a bench.

Scope note: this file asserts the *shape* of the schema and the *shape* of the source. It
cannot assert behaviour. The permission logic itself is
``tests/test_chat_permissions_bench.py``, which needs a bench and therefore **does not run
in CI** — see ``erpnext_enhancements/chat/README.md``.

Run: python -m unittest erpnext_enhancements.tests.test_chat_guardrails
"""

from __future__ import annotations

import ast
import json
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

APP_DIR: Path = Path(__file__).resolve().parents[1]
CHAT_DIR: Path = APP_DIR / "chat"
CHAT_DOCTYPE_DIR: Path = CHAT_DIR / "doctype"
HOOKS_PY: Path = APP_DIR / "hooks.py"

#: The module every chat DocType must declare. ``tests/test_doctype_modules.py`` already
#: asserts the *filesystem* mapping (directory <-> module); this file asserts the JSON
#: field, because a DocType can sit in the right folder and still declare the wrong owner.
CHAT_MODULE: str = "Chat"

#: DocTypes that must exist for the assertions below to mean anything. A broken glob or a
#: renamed directory would otherwise turn this whole suite into a green no-op — which is
#: the exact failure mode that let a pytest suite run nowhere in this repo for weeks.
REQUIRED_DOCTYPES: frozenset[str] = frozenset(
	{
		"Chat Room",
		"Chat Room Member",
		"Chat Message",
		"Chat Mention",
		"Chat Settings",
	}
)

#: ADR §F.18.4 as amended by its own seam note at §H.4.2. **Every chat DocType ships an
#: empty ``permissions`` array except these three.** The values are the complete expected
#: grant, expressed as ``{role: {rights}}``; anything not listed must be absent or 0.
#:
#: ``Chat Retrieval Audit`` is a Phase 5 DocType and does not exist yet. It is listed
#: anyway so that the phase which creates it lands on the ADR's shape rather than on
#: whatever felt reasonable that afternoon; the assertion simply does not fire until the
#: JSON appears.
#: The audit tables, which are the one place in this package where "narrow the list to your
#: own rooms" is the wrong instinct: the rows worth reading are the ones where the reader was
#: NOT a member. Named here so the exception below is a list of two rather than a predicate
#: somebody has to re-derive.
_AUDIT_DOCTYPES: frozenset[str] = frozenset({"Chat Retrieval Audit", "Chat Retrieval Audit Room"})

EXPECTED_DOCPERMS: dict[str, dict[str, frozenset[str]]] = {
	# §H.4.2: the socket join is keyed on the room, so this is the one DocType that MUST
	# be permission-resolvable — a zero-DocPerm doctype cannot be doc-room-joined by
	# anyone but Administrator, and the refusal is silent. Read only: a room record
	# carries a title and membership metadata, never message text.
	"Chat Room": {"Chat User": frozenset({"read"})},
	# §F.18.4: a Single with no DocPerm cannot be opened at all, and this Single is the
	# kill switch for the entire feature.
	"Chat Settings": {"System Manager": frozenset({"read", "write"})},
	# §F.12/§F.18.4: an audit log nobody can read is not an audit log.
	"Chat Retrieval Audit": {"System Manager": frozenset({"read", "report"})},
}

#: The DocPerm keys that grant something. Everything else on a perm row (``role``,
#: ``permlevel``, ``if_owner`` and friends) is a qualifier, not a grant.
_PERM_RIGHTS: tuple[str, ...] = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"print",
	"email",
	"share",
	"set_user_permissions",
)

#: The one module allowed to name the Google Chat API host. ADR §D.5 / the module README:
#: one module means one place to reason about identity, quota and retry.
TRANSPORT_MODULE: Path = CHAT_DIR / "gchat" / "client.py"
GOOGLE_CHAT_HOST: str = "chat.googleapis.com"

#: Every host this app is allowed to speak to at Google, not just the Chat API one.
#:
#: Pinning ``chat.googleapis.com`` alone made the guardrail answer a narrower question than
#: the invariant it advertises. "The client is the only place that speaks HTTP to Google"
#: is not enforced by watching one hostname: a ``requests.post`` to
#: ``oauth2.googleapis.com`` is a second, unreviewed path to Google that the old check could
#: not see, and that is exactly what ``smoke_test.py`` grew. So the check is over the whole
#: set of hosts this integration has any business naming.
#:
#: ``www.googleapis.com`` is deliberately absent: it is the OAuth *scope* namespace and the
#: x509 certificate endpoint, neither of which is a call this rule is about.
PERMITTED_GOOGLE_HOSTS: frozenset[str] = frozenset(
	{
		# the Chat API itself — client.py
		"chat.googleapis.com",
		# the Pub/Sub pull consumer — sync/pubsub.py. A THIRD service, again on its own host.
		# Listed rather than left unnamed on purpose: this check can only catch a host it knows
		# about, so a new Google surface that is not added here is a call path the guardrail
		# silently stops guarding.
		"pubsub.googleapis.com",
		# the DWD assertion exchange — auth.py
		"oauth2.googleapis.com",
		# signJwt, the keyless half of the design — auth.py
		"iamcredentials.googleapis.com",
		# the VM's own ADC token — auth.py
		"metadata.google.internal",
		# Workspace Events subscriptions — events_client.py. A DIFFERENT service on a
		# different host from the Chat API, and it is here for the same reason
		# chat.googleapis.com is: an expired subscription is permanently deleted and cannot
		# be renewed, so the code that manages one must be findable in a single place.
		"workspaceevents.googleapis.com",
	}
)

#: The modules permitted to name one of :data:`PERMITTED_GOOGLE_HOSTS` in a live string.
#: Four, because identity, transport, subscription lifecycle and event delivery are genuinely
#: four concerns: ``auth.py`` mints tokens and never calls an API; ``client.py`` calls the Chat
#: API and never mints; ``events_client.py`` talks to a different service entirely; and
#: ``sync/pubsub.py`` drains the topic those subscriptions publish to, which is a third service
#: again and is authenticated as the **VM** rather than as any coworker.
TRANSPORT_MODULES: frozenset[Path] = frozenset(
	{
		CHAT_DIR / "gchat" / "client.py",
		CHAT_DIR / "gchat" / "auth.py",
		CHAT_DIR / "gchat" / "events_client.py",
		CHAT_DIR / "sync" / "pubsub.py",
	}
)

#: Named exceptions to the rule above, each with the reason it is tolerated. **Not a
#: place to put new code.** An entry here is a hole in the containment argument, so it
#: carries the fix that would close it.
HOST_LITERAL_EXCEPTIONS: dict[Path, str] = {
	CHAT_DIR
	/ "gchat"
	/ "smoke_test.py": (
		"_token_introspection POSTs the freshly minted token to oauth2.googleapis.com/tokeninfo "
		"to read the scopes DWD actually granted, which is the single most common DWD "
		"misconfiguration and is otherwise a 403 four steps later. smoke_test.py is a "
		"`bench execute` script registered in no hook, no scheduler and no endpoint, so it is "
		"not a live call path. It is listed here rather than silently permitted because the "
		"right shape is a named introspection helper in auth.py, which already owns that host "
		"— do that and delete this entry; do not add a second one."
	),
}

#: CHAT-RT-2. Both names are special-cased inside ``frappe.publish_realtime`` and
#: **overwrite an explicitly passed** ``room=`` before the ``if not room:`` guard runs.
FORBIDDEN_REALTIME_EVENTS: frozenset[str] = frozenset({"list_update", "docinfo_update"})

#: Dotted names for the import walk below.
APP_PACKAGE: str = "erpnext_enhancements"
CHAT_PACKAGE: str = f"{APP_PACKAGE}.chat"
TRANSPORT_DOTTED: str = f"{CHAT_PACKAGE}.gchat.client"


# --- loading -----------------------------------------------------------------


def _load_chat_doctypes() -> dict[str, dict[str, Any]]:
	"""Return ``{doctype name: parsed JSON}`` for every DocType JSON under ``chat/``."""
	found: dict[str, dict[str, Any]] = {}
	for path in sorted(CHAT_DOCTYPE_DIR.glob("*/*.json")):
		try:
			data = json.loads(path.read_text(encoding="utf-8"))
		except ValueError:  # pragma: no cover - a malformed JSON fails test_discovery
			continue
		if data.get("doctype") != "DocType":
			continue
		data["__path__"] = str(path)
		found[data.get("name", path.stem)] = data
	return found


def _chat_python_files() -> list[Path]:
	"""Every Python module under ``chat/``, ``__pycache__`` excluded."""
	return sorted(p for p in CHAT_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
	return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


DOCTYPES: dict[str, dict[str, Any]] = _load_chat_doctypes()


# --- ast helpers -------------------------------------------------------------


def _calls_named(tree: ast.Module, names: frozenset[str]) -> Iterator[ast.Call]:
	"""Yield every call whose callee is one of ``names``, dotted or bare."""
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		func = node.func
		if isinstance(func, ast.Attribute) and func.attr in names:
			yield node
		elif isinstance(func, ast.Name) and func.id in names:
			yield node


def _docstring_ids(tree: ast.Module) -> set[int]:
	"""Object ids of every docstring constant, so prose can be excluded from scans.

	A module that *explains* the transport in its docstring is doing the right thing;
	a module that *contains* the host in a live string literal is the violation. Nothing
	here looks at comments — ``ast`` discards them, which is the behaviour we want.
	"""
	ids: set[int] = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			continue
		body = getattr(node, "body", None)
		if not body:
			continue
		first = body[0]
		if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
			if isinstance(first.value.value, str):
				ids.add(id(first.value))
	return ids


def _live_strings(tree: ast.Module) -> Iterator[tuple[int, str]]:
	"""Yield ``(lineno, value)`` for every string constant that is not a docstring."""
	skip = _docstring_ids(tree)
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
			yield node.lineno, node.value


def _hooks_dict(name: str) -> dict[str, Any]:
	"""Read one top-level dict literal out of ``hooks.py`` without importing it.

	``hooks.py`` imports ``frappe`` at module scope, so this suite reads it the way
	``tests/test_hooks_integrity.py`` does — as source.
	"""
	tree = ast.parse(HOOKS_PY.read_text(encoding="utf-8"), filename=str(HOOKS_PY))
	for node in tree.body:
		if not isinstance(node, ast.Assign):
			continue
		target = node.targets[0]
		if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Dict):
			try:
				return ast.literal_eval(node.value)
			except ValueError:  # pragma: no cover - a non-literal hook dict
				return {}
	return {}


def _module_file(dotted: str) -> Path | None:
	"""The file behind a first-party dotted module name — module or package.

	``None`` when the name is not ours or does not resolve, which is the right answer for
	``frappe.utils.something`` and for a handler pointing at a module Phase 2 has not
	written yet. ``tests/test_hook_targets_resolve.py`` is what fails on the second case;
	this walk just stops.
	"""
	if dotted != APP_PACKAGE and not dotted.startswith(f"{APP_PACKAGE}."):
		return None
	relative = dotted[len(APP_PACKAGE) :].lstrip(".").replace(".", "/")
	if not relative:
		candidates = [APP_DIR / "__init__.py"]
	else:
		candidates = [APP_DIR / f"{relative}.py", APP_DIR / relative / "__init__.py"]
	for candidate in candidates:
		if candidate.is_file():
			return candidate
	return None


def _imports_of(dotted: str, path: Path) -> set[str]:
	"""Every first-party dotted module named by an import anywhere in ``path``.

	Three details, each of which a narrower version would miss:

	* ``from X import Y`` yields **both** ``X`` and ``X.Y``. ``from ...gchat import client``
	  is how a module reaches the transport without the string ``gchat.client`` ever
	  appearing in its source, and an import scan that only reads ``node.module`` sees a
	  package import and shrugs.
	* **relative** imports are resolved against the importer's own package, because
	  ``from .client import GoogleChatClient`` is the form a module inside ``gchat/``
	  would naturally write.
	* **function-local** imports count. ``ast.walk`` ignores scope, and so does Google's
	  per-space write quota — deferring the import does not defer the HTTP call.
	"""
	package = dotted if path.name == "__init__.py" else dotted.rpartition(".")[0]
	found: set[str] = set()
	for node in ast.walk(_parse(path)):
		if isinstance(node, ast.Import):
			found.update(alias.name for alias in node.names)
		elif isinstance(node, ast.ImportFrom):
			if node.level:
				parts = package.split(".")
				keep = len(parts) - (node.level - 1)
				if keep <= 0:
					continue
				base = ".".join(parts[:keep])
			else:
				base = ""
			if base and node.module:
				module = f"{base}.{node.module}"
			else:
				module = node.module or base
			if not module:
				continue
			found.add(module)
			found.update(f"{module}.{alias.name}" for alias in node.names)
	return {m for m in found if m == APP_PACKAGE or m.startswith(f"{APP_PACKAGE}.")}


def _first_party_closure(dotted: str) -> set[str]:
	"""``dotted`` plus every first-party module reachable from it by import, transitively."""
	seen: set[str] = set()
	queue: list[str] = [dotted]
	while queue:
		current = queue.pop()
		if current in seen:
			continue
		seen.add(current)
		path = _module_file(current)
		if path is not None:
			queue.extend(_imports_of(current, path))
	return seen


def _reaches_transport(dotted: str) -> bool:
	"""Does ``dotted``, or anything it imports at any depth, reach the Google transport?"""
	return any(
		name == TRANSPORT_DOTTED or name.startswith(f"{TRANSPORT_DOTTED}.")
		for name in _first_party_closure(dotted)
	)


def _resolvable_module_of(dotted_handler: str) -> str:
	"""The module part of a ``pkg.mod.func`` hook target, walked up until a file exists."""
	candidate = dotted_handler.rpartition(".")[0]
	while candidate and _module_file(candidate) is None:
		candidate = candidate.rpartition(".")[0]
	return candidate


def _chat_doc_event_handlers() -> list[str]:
	"""Every ``doc_events`` target in ``hooks.py`` that lives in the chat package."""
	handlers: set[str] = set()
	for _doctype, events in _hooks_dict("doc_events").items():
		if not isinstance(events, dict):
			continue
		for _event, target in events.items():
			targets = target if isinstance(target, list | tuple) else [target]
			handlers.update(
				t
				for t in targets
				if isinstance(t, str) and (t == CHAT_PACKAGE or t.startswith(f"{CHAT_PACKAGE}."))
			)
	return sorted(handlers)


def _granted_rights(perm_row: dict[str, Any]) -> frozenset[str]:
	return frozenset(right for right in _PERM_RIGHTS if perm_row.get(right))


def _perm_map(data: dict[str, Any]) -> dict[str, frozenset[str]]:
	"""``{role: {rights}}`` for a DocType JSON, ignoring rows that grant nothing."""
	out: dict[str, frozenset[str]] = {}
	for row in data.get("permissions") or []:
		rights = _granted_rights(row)
		if rights:
			out[row.get("role", "")] = rights
	return out


# --- discovery ---------------------------------------------------------------


class TestDiscovery(unittest.TestCase):
	"""Without these, every assertion below can pass by finding nothing."""

	def test_chat_doctypes_were_found(self) -> None:
		self.assertTrue(
			DOCTYPES,
			f"no chat DocType JSON was discovered under {CHAT_DOCTYPE_DIR}. Every other "
			"assertion in this file iterates that glob, so an empty result turns the whole "
			"guardrail suite green while guarding nothing. Fix the path, do not delete the test.",
		)

	def test_the_named_doctypes_exist(self) -> None:
		missing = sorted(REQUIRED_DOCTYPES - set(DOCTYPES))
		self.assertFalse(
			missing,
			f"these chat DocTypes are named by assertions in this file but were not found: {missing}. "
			"If one was deliberately renamed or removed, update REQUIRED_DOCTYPES in the same commit "
			"— otherwise its guardrails silently stop running.",
		)

	def test_chat_python_files_were_found(self) -> None:
		self.assertTrue(
			_chat_python_files(),
			f"no Python modules found under {CHAT_DIR}. The source-level guards below would all pass vacuously.",
		)


# --- layer 1: the permission model -------------------------------------------


class TestZeroDocPerm(unittest.TestCase):
	"""ADR §F.18 Layer 1, and the single most likely regression in the whole feature.

	Zero DocPerm is what closes ``/api/resource/Chat Message``, the desk list and form
	views, report view, export, global search, and the generic MCP read tools. It is a new
	precedent in this app — 0 of 187 existing DocTypes ship an empty ``permissions`` array —
	so the pressure to "just add a System Manager row so I can look at a message" is real
	and will arrive as a one-line diff.
	"""

	def test_only_the_adr_exemptions_carry_docperm(self) -> None:
		for name, data in sorted(DOCTYPES.items()):
			with self.subTest(doctype=name):
				actual = _perm_map(data)
				expected = EXPECTED_DOCPERMS.get(name, {})
				self.assertEqual(
					actual,
					expected,
					f"{name} ({data['__path__']}) has DocPerms {actual or '{}'}, expected "
					f"{expected or '{} (an EMPTY permissions array)'}.\n"
					"ADR §F.18.1 Layer 1: every chat content DocType ships zero DocPerm, and §H.4.2 "
					"amends that to exactly three exemptions — Chat Room (minimal read, because the "
					"socket doc-room join is the only thing that must be permission-resolvable), "
					"Chat Settings (a Single with no DocPerm cannot be opened) and Chat Retrieval "
					"Audit (an audit log nobody can read is not an audit log).\n"
					"Adding a read row to any other chat DocType re-opens the desk, the REST resource "
					"API, export and the generic MCP read tools over employee-private message bodies. "
					"If you genuinely need one, ADR §F.18.4's standing obligation applies: the SAME "
					"commit adds both a permission_query_conditions entry and its has_permission twin, "
					"and a bench test modelled on tests/test_kpi_snapshot_permissions.py. Update "
					"EXPECTED_DOCPERMS deliberately; do not loosen this assertion.",
				)

	def test_chat_room_is_read_only_for_the_chat_role(self) -> None:
		"""The one exemption, pinned separately so a widening reads as its own failure."""
		room = DOCTYPES.get("Chat Room", {})
		rows = room.get("permissions") or []
		self.assertEqual(
			len(rows),
			1,
			f"Chat Room must carry exactly one DocPerm row, found {len(rows)}: {rows}. "
			"§H.4.2 buys back the minimum needed to make the doc-room join resolvable and nothing more.",
		)
		granted = _granted_rights(rows[0]) if rows else frozenset()
		self.assertEqual(
			granted,
			frozenset({"read"}),
			f"Chat Room's DocPerm grants {sorted(granted)}. It may grant READ and nothing else — "
			"no write, no create, no delete, no report, no export. A room is created by the "
			"provisioning path, never by a person in the desk, and export would hand the full room "
			"list (titles, linked documents, Google space names) to anyone holding the chat role.",
		)

	def test_every_docperm_bearing_chat_doctype_has_both_hooks(self) -> None:
		"""ADR §F.18.4's standing obligation, and the house 10-and-10 parity doctrine.

		A ``has_permission`` hook can only *restrict* what a DocPerm already grants, so the
		DocPerm row is the outer bound and the pair is the only thing between a
		member-scoped read and a full-table read. Parity is enforced here rather than
		trusted, because the failure is a silent widening.
		"""
		queries = _hooks_dict("permission_query_conditions")
		single_doc = _hooks_dict("has_permission")
		for name, data in sorted(DOCTYPES.items()):
			perms = _perm_map(data)
			if not perms:
				continue
			if data.get("issingle"):
				# A Single has exactly one row and no list view; a query condition has
				# nothing to filter.
				continue
			if name in _AUDIT_DOCTYPES and set(perms) <= {"System Manager"}:
				# §F.12's stated exception, and it is conditional rather than blanket. An audit
				# log exists to be read across every row — a query condition narrowing it to
				# "rooms you are in" would hide precisely the non-participant reads it was
				# written to surface — and `System Manager` sees all rows by design, so there is
				# nothing for the pair to add.
				#
				# The condition is the load-bearing half: the ADR says the obligation fires "the
				# moment a narrower role is granted read here", so this exemption evaporates as
				# soon as the perm map names anyone else. Phase 6 grants the oversight role
				# `read` with `export = 0`, and on that commit both hooks must ship.
				continue
			with self.subTest(doctype=name):
				self.assertIn(
					name,
					queries,
					f"{name} carries a read DocPerm but has no permission_query_conditions entry in "
					"hooks.py. The DocPerm makes the whole table listable; the query condition is what "
					"narrows it to the caller's own rooms. (ADR §F.18.4 standing obligation.)",
				)
				self.assertIn(
					name,
					single_doc,
					f"{name} carries a read DocPerm and a permission_query_conditions entry but no "
					"has_permission twin. The query condition only filters LIST reads — the "
					"single-document path (/app/<doctype>/<name>, frappe.get_doc, the socket "
					"doc_subscribe callback) consults has_permission instead, so without the twin a "
					"non-member can open any single record by guessing its name. Parity is the house "
					"doctrine (hooks.py's register is at exact 10-and-10) and it holds without exception.",
				)


class TestNoPasswordFields(unittest.TestCase):
	"""The no-secrets rule, made structural rather than aspirational.

	ADR §F.13: the Google integration is keyless — domain-wide delegation signed by the
	VM-attached service account through IAM Credentials ``signJwt``. Chat Settings holds
	identifiers only: project ids, service-account *emails*, topic names, the audience URL.
	A ``Password`` field is where a service-account JSON goes when a human is in a hurry,
	and once it is in ``__Auth`` it is in every backup.
	"""

	def test_no_chat_doctype_declares_a_password_field(self) -> None:
		for name, data in sorted(DOCTYPES.items()):
			offenders = [
				f.get("fieldname") for f in data.get("fields") or [] if f.get("fieldtype") == "Password"
			]
			with self.subTest(doctype=name):
				self.assertFalse(
					offenders,
					f"{name} ({data['__path__']}) declares Password field(s) {offenders}. "
					"No chat DocType may hold a credential. If you are adding one, the design has "
					"gone wrong upstream: the whole point of the keyless DWD path (ADR §F.13, §G.2) "
					"is that there is no key to store, no rotation to schedule and nothing to leak "
					"in a backup. Store the identifier, mint the token at call time.",
				)


class TestModuleDeclaration(unittest.TestCase):
	def test_every_chat_doctype_declares_the_chat_module(self) -> None:
		"""``tests/test_doctype_modules.py`` asserts the folder; this asserts the field.

		They are different failures. A DocType can sit in ``chat/doctype/`` with
		``"module": "Integration Hub"`` — that test compares the two and would catch it —
		but this one names the intended value, so a *coordinated* wrong move (both the
		folder and the field) still fails here.
		"""
		for name, data in sorted(DOCTYPES.items()):
			with self.subTest(doctype=name):
				self.assertEqual(
					data.get("module"),
					CHAT_MODULE,
					f"{name} ({data['__path__']}) declares module {data.get('module')!r}, expected "
					f"{CHAT_MODULE!r}. The module decides who owns the DocType at migrate time and "
					"which app's uninstall removes it.",
				)


# --- the hot table: Chat Message ---------------------------------------------


class TestChatMessageSchema(unittest.TestCase):
	"""Four properties of the hot table that are effectively unfixable after data lands."""

	def setUp(self) -> None:
		self.data: dict[str, Any] = DOCTYPES.get("Chat Message", {})
		self.path: str = self.data.get("__path__", "<missing>")

	def _field(self, fieldname: str) -> dict[str, Any]:
		for field in self.data.get("fields") or []:
			if field.get("fieldname") == fieldname:
				return field
		self.fail(f"Chat Message has no field {fieldname!r} ({self.path})")
		raise AssertionError  # pragma: no cover - self.fail raises

	def test_autoname_is_hash(self) -> None:
		self.assertEqual(
			self.data.get("autoname"),
			"hash",
			f"Chat Message must use autoname 'hash' (got {self.data.get('autoname')!r}, {self.path}). "
			"A naming series is a read-modify-write on tabSeries inside every insert, which serialises "
			"inserts and invites lock waits on the busiest table in the feature; autoincrement is not "
			"gap-safe, is hard to migrate away from, and leaks message volume to anyone who can see a "
			"name. Raven — the production Frappe chat app — uses hash on its message DocType. This is "
			"unfixable after data lands: changing it renames every row and breaks every stored link.",
		)

	def test_naming_rule_is_random(self) -> None:
		self.assertEqual(
			self.data.get("naming_rule"),
			"Random",
			f"Chat Message must declare naming_rule 'Random' to match autoname 'hash' (got "
			f"{self.data.get('naming_rule')!r}, {self.path}). Frappe's DocType form rewrites autoname "
			"from naming_rule on save, so a JSON where the two disagree silently loses the autoname the "
			"next time anyone opens the DocType in the desk.",
		)

	def test_sort_field_is_creation_and_never_modified(self) -> None:
		sort_field = self.data.get("sort_field")
		self.assertEqual(
			sort_field,
			"creation",
			f"Chat Message must sort by 'creation' (got {sort_field!r}, {self.path}). Raven's DocType "
			"JSON defaults to modified DESC, which is fine for a list view and wrong for a transcript: "
			"editing a message from last March would teleport it to the bottom of today's conversation. "
			"If you copied a Raven JSON, this is the field to fix.",
		)
		self.assertNotEqual(
			sort_field,
			"modified",
			"Chat Message sorts by 'modified'. See above — an edit reorders history.",
		)

	def test_track_changes_is_explicitly_off(self) -> None:
		self.assertIn(
			"track_changes",
			self.data,
			f"Chat Message must set track_changes EXPLICITLY ({self.path}). Frappe's default for new "
			"DocTypes is recorded as unverified (R02-V04), and relying on a default is relying on "
			"something nobody has checked.",
		)
		self.assertEqual(
			self.data.get("track_changes"),
			0,
			f"Chat Message must set track_changes = 0 (got {self.data.get('track_changes')!r}, "
			f"{self.path}). Every tracked edit writes a Version row carrying TWO full copies of the "
			"message body. tabVersion is already this site's busiest table by write rate and its third "
			"largest, it has no retention rule in Logs To Clear, and the MCP run_database_query surface "
			"can read it — so track_changes = 1 both amplifies the write load and routes message bodies "
			"around Layer 1. Edit history that matters lives on the row itself (is_edited, edited_at).",
		)

	def test_no_field_is_flagged_for_global_search(self) -> None:
		offenders = [
			f.get("fieldname") for f in self.data.get("fields") or [] if f.get("in_global_search")
		]
		self.assertFalse(
			offenders,
			f"Chat Message fields {offenders} are flagged in_global_search ({self.path}). "
			"__global_search on this site is already 761k rows / 151.5 MB and the fourth-largest table; "
			"message bodies would make it the largest inside a year. Worse, it copies chat text into a "
			"search surface whose permission behaviour under a zero-DocPerm DocType nobody has verified, "
			"and pollutes the awesomebar with chatter. Chat search is served by the retrieval package "
			"(ADR §F.11), which is membership-filtered by construction.",
		)

	def test_index_web_pages_for_search_is_off(self) -> None:
		self.assertEqual(
			self.data.get("index_web_pages_for_search"),
			0,
			f"Chat Message must set index_web_pages_for_search = 0 (got "
			f"{self.data.get('index_web_pages_for_search')!r}, {self.path}). Raven sets this to 1 on "
			"Raven Channel; do not copy that. It writes message content into the website search index, "
			"which is a second uncontrolled copy of the same private text.",
		)

	def test_comments_and_view_tracking_are_off(self) -> None:
		"""The Phase 1 prompt's "comments are disabled" item, encoded as what Frappe exposes.

		Frappe v16's DocType has **no ``allow_comments`` property** — there is nothing to
		set. What is actually expressible is: no timeline events, no seen/view tracking,
		and (the real closure) zero DocPerm, which means no desk form and therefore no
		comment box to type into. All three are asserted here so that "comments are
		disabled" cannot quietly become untrue by someone granting a read DocPerm and
		re-opening the form — that widening fails in TestZeroDocPerm above, which is the
		assertion that actually enforces this one.
		"""
		for prop in ("allow_events_in_timeline", "track_seen", "track_views"):
			with self.subTest(property=prop):
				self.assertEqual(
					self.data.get(prop),
					0,
					f"Chat Message must set {prop} = 0 explicitly (got {self.data.get(prop)!r}, "
					f"{self.path}). _comments, _seen and _liked_by on a message row are meaningless "
					"— every one of them is an extra write per read on the hottest table in the app, "
					"and _seen in particular grows without bound in a room with twenty members.",
				)

	def test_gchat_message_name_is_unique_and_wide_enough(self) -> None:
		field = self._field("gchat_message_name")
		self.assertEqual(
			field.get("fieldtype"),
			"Data",
			f"Chat Message.gchat_message_name must be Data (got {field.get('fieldtype')!r}). "
			"Only Data can carry a DocField-level unique constraint that Frappe emits as a real "
			"index; Small Text and friends cannot be indexed as a whole column in MariaDB.",
		)
		self.assertEqual(
			field.get("unique"),
			1,
			"Chat Message.gchat_message_name must declare unique = 1 on the DocField. This is "
			"invariant I2 made STRUCTURAL: inbound dedupe is one probe and a duplicate is a database "
			"error, not a race the application has to win. It is declared here rather than only in "
			"add_chat_indexes.py deliberately — with the constraint added only by frappe.db.add_unique, "
			"Frappe stores '' rather than NULL for unrelayed rows, and the SECOND unrelayed message in "
			"the entire table fails to insert.",
		)
		length = field.get("length")
		self.assertTrue(
			length,
			"Chat Message.gchat_message_name declares no length, so Frappe creates varchar(140). "
			"Google's resource name is spaces/{space}/messages/{message} and exceeds that; MariaDB "
			"truncates on insert, and two DISTINCT Google messages then collide on the unique index. "
			"The visible symptom is a message that never arrives. Set length: 255 (ADR §F.6, B3).",
		)
		self.assertNotEqual(
			length,
			140,
			"Chat Message.gchat_message_name is pinned at the varchar(140) default. See above: "
			"truncate-collision is silent message loss, not a warning.",
		)
		self.assertEqual(
			length,
			255,
			f"Chat Message.gchat_message_name must be length 255 (got {length!r}) — the value ADR §F.6 "
			"fixes, and the same width used for gchat_thread_name and gchat_space_name so the three "
			"resource names cannot disagree about what fits.",
		)

	def test_chat_message_is_not_a_child_table(self) -> None:
		self.assertFalse(
			self.data.get("istable"),
			f"Chat Message must NOT be a child table ({self.path}). Frappe loads child tables in FULL "
			"with their parent, so frappe.get_doc on a room would materialise every message ever sent "
			"in it. Child rows also carry parent/parenttype/parentfield/idx and are re-idx'd on every "
			"parent save, which makes append-heavy use pathological. A message is a document with its "
			"own name, its own permissions story and its own retention rule.",
		)


class TestChatMentionIsAChildTable(unittest.TestCase):
	def test_chat_mention_is_a_child_table(self) -> None:
		data = DOCTYPES.get("Chat Mention", {})
		self.assertEqual(
			data.get("istable"),
			1,
			f"Chat Mention must be a child table (istable = 1), got {data.get('istable')!r} "
			f"({data.get('__path__')}). Mentions are bounded, small, always read with their message and "
			"never queried on their own — which is the one shape a child table is correct for. Making "
			"it a standalone DocType adds a name, a permissions story and a join to every render.",
		)


# --- source-level guards ------------------------------------------------------


class TestRealtimeHygiene(unittest.TestCase):
	"""CHAT-RT-1 and CHAT-RT-2 (ADR §F.18.3, §H.4.4), linted rather than remembered."""

	def test_every_publish_realtime_targets_a_room_explicitly(self) -> None:
		offenders: list[str] = []
		for path in _chat_python_files():
			tree = _parse(path)
			for call in _calls_named(tree, frozenset({"publish_realtime"})):
				kwargs = {kw.arg for kw in call.keywords if kw.arg}
				if "room" not in kwargs:
					rel = path.relative_to(APP_DIR)
					offenders.append(f"{rel}:{call.lineno} (kwargs: {sorted(kwargs) or 'none'})")
		self.assertFalse(
			offenders,
			"these chat publish_realtime calls do not pass room= explicitly: "
			+ "; ".join(offenders)
			+ ".\nCHAT-RT-1: every chat realtime publish passes room=, computed by the single "
			"chat-owned helper — never bare user=, never doctype=+docname=. Two failure modes make "
			"this non-negotiable. (1) publish_realtime's final fallback is get_site_room() = 'all', a "
			"room every logged-in System User is already sitting in, so a call that forgets its "
			"targeting argument broadcasts message bodies to every connected session. (2) Inside a "
			"background job with frappe.local.task_id set, a call passing user= and no room= is "
			"silently retargeted to task_progress:<task_id> — a room ANY client may join with no "
			"permission check at all. Chat relay work happens in background jobs, so (2) is the live one.",
		)

	def test_no_chat_event_is_named_list_update_or_docinfo_update(self) -> None:
		offenders: list[str] = []
		for path in _chat_python_files():
			tree = _parse(path)
			for call in _calls_named(tree, frozenset({"publish_realtime"})):
				event: str | None = None
				if call.args and isinstance(call.args[0], ast.Constant):
					if isinstance(call.args[0].value, str):
						event = call.args[0].value
				for kw in call.keywords:
					if kw.arg == "event" and isinstance(kw.value, ast.Constant):
						if isinstance(kw.value.value, str):
							event = kw.value.value
				if event in FORBIDDEN_REALTIME_EVENTS:
					offenders.append(f"{path.relative_to(APP_DIR)}:{call.lineno} -> {event!r}")
		self.assertFalse(
			offenders,
			"these chat realtime events use a reserved name: "
			+ "; ".join(offenders)
			+ f".\nCHAT-RT-2: no chat event may be named any of {sorted(FORBIDDEN_REALTIME_EVENTS)}. "
			"Frappe special-cases both names and UNCONDITIONALLY overwrites an explicitly passed room= "
			"before the `if not room:` guard runs — so the one call you targeted most carefully is "
			"exactly the one that escapes. Name the event chat:<something>.",
		)


class TestEnqueueAfterCommit(unittest.TestCase):
	def test_every_chat_enqueue_passes_enqueue_after_commit(self) -> None:
		"""A job that starts before its transaction commits reads a row that is not there.

		Frappe's worker pool is a different process against the same database. Without
		``enqueue_after_commit=True`` the job is dispatched the moment ``enqueue`` returns,
		which is *inside* the request transaction — so the row it was queued to process
		does not exist yet, and the failure is a 1-in-N "job says the message vanished"
		that never reproduces locally.
		"""
		offenders: list[str] = []
		for path in _chat_python_files():
			tree = _parse(path)
			for call in _calls_named(tree, frozenset({"enqueue", "enqueue_doc"})):
				after_commit = None
				for kw in call.keywords:
					if kw.arg == "enqueue_after_commit":
						after_commit = kw.value
				rel = f"{path.relative_to(APP_DIR)}:{call.lineno}"
				if after_commit is None:
					offenders.append(f"{rel} (no enqueue_after_commit)")
				elif not (isinstance(after_commit, ast.Constant) and after_commit.value is True):
					offenders.append(f"{rel} (enqueue_after_commit is not a literal True)")
		self.assertFalse(
			offenders,
			"these chat enqueue calls are missing enqueue_after_commit=True: "
			+ "; ".join(offenders)
			+ ".\nPass it as a literal True, not a variable — this is a lint, and a lint that has to "
			"resolve a name is a lint that can be fooled. Note the related hazard the relay design "
			"already accounts for: a production deploy FLUSHDBs the queue Redis, so an enqueued job "
			"can vanish entirely. That is why durable work is a Chat Relay Job row, not an enqueue.",
		)


class TestTransportIsOneModule(unittest.TestCase):
	def test_only_the_transport_modules_name_a_google_host(self) -> None:
		"""Two modules speak HTTP to Google: ``gchat/client.py`` and ``gchat/auth.py``.

		Docstrings and comments that *discuss* a host are fine and expected — the scan
		below deliberately ignores them. What it catches is a live string literal, which
		is what a second, unreviewed call path looks like on the day somebody adds one.

		The host **set** rather than the Chat host alone: the invariant is "this app has
		two places that speak to Google", and a check that watches one hostname does not
		enforce it. A ``requests.post`` to ``oauth2.googleapis.com`` in a third module is
		precisely as much of a containment breach as one to ``chat.googleapis.com``, and
		the narrower check could not see it.
		"""
		offenders: list[str] = []
		for path in _chat_python_files():
			if path in TRANSPORT_MODULES or path in HOST_LITERAL_EXCEPTIONS:
				continue
			for lineno, value in _live_strings(_parse(path)):
				for host in sorted(PERMITTED_GOOGLE_HOSTS):
					if host in value:
						offenders.append(f"{path.relative_to(APP_DIR)}:{lineno} -> {host}")
		permitted = ", ".join(sorted(str(p.relative_to(APP_DIR)) for p in TRANSPORT_MODULES))
		self.assertFalse(
			offenders,
			"a Google host appears in a live string outside "
			f"{permitted}: " + "; ".join(offenders) + ".\n"
			f"The hosts this app may name are {sorted(PERMITTED_GOOGLE_HOSTS)}, and only "
			"gchat/client.py (the Chat API) and gchat/auth.py (tokens: the assertion exchange, "
			"signJwt, and the VM's own ADC) may name them.\n"
			"Few modules means one place to reason about identity (human attribution vs the app "
			"identity), one place that enforces the 1-write-per-second-per-space quota, one place that "
			"retries, and a bounded set a reviewer has to read to know everything this app sends to "
			"Google. A second call site is how a message gets posted as the bot, or twice, or "
			"unthrottled — and a second *token* call site is how an unreviewed scope grant appears.\n"
			"If you need this, put it behind a named helper in the module that already owns the host. "
			"HOST_LITERAL_EXCEPTIONS is not the answer; read the note on it first.",
		)

	def test_every_host_literal_exception_still_exists_and_is_justified(self) -> None:
		"""An exception list that outlives the file it excuses is a hole nobody is watching."""
		for path, reason in sorted(HOST_LITERAL_EXCEPTIONS.items()):
			with self.subTest(path=str(path)):
				self.assertTrue(
					path.exists(),
					f"HOST_LITERAL_EXCEPTIONS names {path}, which does not exist. A stale entry "
					"silently exempts nothing today and whatever takes that filename tomorrow. "
					"Delete it in the commit that deletes the file.",
				)
				self.assertTrue(
					len(reason.strip()) >= 80,
					f"the HOST_LITERAL_EXCEPTIONS entry for {path} has no real justification. "
					"Every exemption states why the call is not a live path and what would close it.",
				)
				self.assertNotIn(
					path,
					TRANSPORT_MODULES,
					f"{path} is both a transport module and an exception; one of the two is wrong.",
				)

	def test_no_doctype_controller_imports_the_transport(self) -> None:
		"""No ``doc_events`` handler may reach Google. This is a quota rule, not a style rule.

		A DocType controller method (``after_insert``, ``on_update``, …) *is* a doc event.
		Google Chat allows **1 write per second per space**, and ``media.upload`` shares
		that bucket — so a synchronous call from a save turns ordinary typing into dropped
		messages, and it does it inside the user's request. Outbound work goes on a
		``Chat Relay Job`` row and is drained by the Phase 2 worker.
		"""
		offenders: list[str] = []
		for path in sorted(CHAT_DOCTYPE_DIR.rglob("*.py")):
			if "__pycache__" in path.parts:
				continue
			tree = _parse(path)
			for node in ast.walk(tree):
				if isinstance(node, ast.ImportFrom) and node.module and "gchat" in node.module:
					offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno} -> {node.module}")
				elif isinstance(node, ast.Import):
					for alias in node.names:
						if "gchat" in alias.name:
							offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno} -> {alias.name}")
		self.assertFalse(
			offenders,
			"these chat DocType controllers import the Google transport package: "
			+ "; ".join(offenders)
			+ ".\nSee the docstring: a doc_events handler that calls Google spends the space's single "
			"write per second inside a user's save. Queue a Chat Relay Job instead.",
		)

	def test_no_hooks_registered_chat_doc_event_reaches_the_transport(self) -> None:
		"""The same rule for handlers registered in ``hooks.py`` rather than on a controller.

		The controller test above is the *shape* Phase 1 happens to have; this is the
		invariant. ``doc_events`` can point anywhere, and the module it points at is very
		unlikely to be under ``chat/doctype/`` — ``erpnext_enhancements.chat.relay.on_insert``
		is exactly what Phase 2 will be tempted to write, and it satisfies every other
		guard in this file: it lives outside ``doctype/``, and it never spells
		``chat.googleapis.com`` because it imports the client instead.

		So the check is **transitive**. A handler that imports a helper that imports the
		client is a handler that calls Google from inside a user's save; one indirection
		does not change what happens to the space's single write per second, and one
		indirection is all a direct-import check costs an author.
		"""
		offenders: list[str] = []
		for dotted in _chat_doc_event_handlers():
			module = _resolvable_module_of(dotted)
			if not module:
				continue
			if _reaches_transport(module):
				chain = sorted(
					name
					for name in _first_party_closure(module)
					if name == TRANSPORT_DOTTED or name.startswith(f"{TRANSPORT_DOTTED}.")
				)
				offenders.append(f"{dotted} (module {module} reaches {', '.join(chain)})")
		self.assertFalse(
			offenders,
			"these hooks.py doc_events handlers reach the Google transport: "
			+ "; ".join(offenders)
			+ ".\nNo Chat API call from any doc_events handler, directly or through a helper — the "
			"space's 1 write/second budget is not the user's save transaction to spend, and a save "
			"that blocks on Google fails when Google is slow. Write a Chat Relay Job row and let the "
			"Phase 2 worker drain it; that is the whole reason the DocType exists.",
		)

	def test_the_transitive_import_walk_is_not_vacuous(self) -> None:
		"""Prove the walker above actually detects things, in both directions.

		The test it backs passes today by finding no chat ``doc_events`` at all, which is
		correct for Phase 1 and indistinguishable from a walker that returns ``False`` for
		everything. That is the failure mode ``TestDiscovery`` exists for, applied to a
		function rather than a glob.
		"""
		smoke = f"{CHAT_PACKAGE}.gchat.smoke_test"
		closure = _first_party_closure(smoke)

		self.assertTrue(
			_reaches_transport(smoke),
			f"the import walk cannot see that {smoke} imports {TRANSPORT_DOTTED}, which it does "
			"at module scope. Every doc_events assertion built on this walk is therefore passing "
			"by finding nothing. Fix the walk, do not delete this test.",
		)
		self.assertIn(
			f"{CHAT_PACKAGE}.gchat.backoff",
			closure,
			f"the import walk is not transitive: {smoke} imports client.py, client.py imports "
			"backoff.py at module scope, and backoff is not in the closure. A one-hop check lets "
			"any handler evade the rule by adding a single helper module in between.",
		)
		self.assertFalse(
			_reaches_transport(f"{CHAT_PACKAGE}.permissions"),
			f"{CHAT_PACKAGE}.permissions is reported as reaching the transport. It imports no "
			"Google code at all, so the walk is over-matching and the guardrail will fire on "
			"innocent modules until somebody weakens it.",
		)


if __name__ == "__main__":  # pragma: no cover
	unittest.main()
