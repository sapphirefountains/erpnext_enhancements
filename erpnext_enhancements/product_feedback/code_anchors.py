# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Where in the code a request points, for the model planning the work (WI-079 slice 3).

``codemap.py`` gives the planner the *shape* of this repository: its modules, the packages
new code lands in, the house rules. That fixed the worst of the blind breakdown, and it
left the next problem in plain view: a request filed from the Item form got a plan written
against a generic idea of an Item, not against the fields, controller and customizations
this site actually has. The map is the same for every request. This is the part that is
not.

--------------------------------------------------------------------------------------
What goes, and what never does
--------------------------------------------------------------------------------------

From the request's captured context, the path and the doctype, it picks what a contributor
would open first: the ``www/`` controller and template behind a web route, the doctype's
field list with its restricted fields flagged, its controller outline, what this app hooks
onto it, the module README, and the recent CHANGELOG sections that name any of them.

**Schema and code facts only, never a document's data.** No field value, no docname, and no
row from any user doctype: besides ``frappe.get_meta``, which reads schema, the only queries
are ``DocType`` (to confirm a name) and ``Property Setter`` (a customization, not a record).
The document name the widget captured is not even selected by ``breakdown.build_payload``
any more, so there is nothing to leak.
The path is reduced the same way: :func:`parse_path` keeps the screen and drops the record,
because ``/desk/item/PUMP-001`` carries a docname in its path as surely as a field would.

--------------------------------------------------------------------------------------
Deterministic, bounded, and never the reason a breakdown failed
--------------------------------------------------------------------------------------

The same request on the same deploy gives byte-identical anchors: no timestamps, and sorted
wherever order carries no meaning. That is what makes "prompt size before and after" a
measurement rather than an anecdote. The whole object is held under ``MAX_ANCHOR_CHARS``
by :func:`fit_to_budget`, which cuts in a fixed order and records what it cut, so the model
is told a listing is partial instead of reading it as complete.

Every step has its own ``try``. A step that fails contributes nothing, and a build that
fails outright returns ``{}``: missing anchors cost accuracy, a raising build would cost the
whole breakdown. There is no cache: a build is a handful of file reads and two small
queries, it runs a few times a day, and property setters can change between deploys.

The pure helpers (:func:`parse_path`, :func:`desk_slug_to_candidates`,
:func:`outline_python`, :func:`changelog_sections`, :func:`changelog_excerpts`,
:func:`readme_sections`, :func:`project_field`, :func:`fit_to_budget`) touch no ``frappe``
state, so the bench-free suite tests them against the real repository files.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import textwrap
from typing import Any
from urllib.parse import unquote, urlsplit

import frappe

APP = "erpnext_enhancements"

#: The whole Anchors object, serialized compactly. Triton caps its rendered section at 45,000
#: characters, so the object always fits the section it becomes.
MAX_ANCHOR_CHARS = 40000

#: An outline is a table of contents, not the file. Core-app files get less room: the planner
#: needs to know what ERPNext's controller does, not how.
MAX_OUTLINE_CHARS = 6000
MAX_CORE_OUTLINE_CHARS = 4000
MAX_DOCSTRING_CHARS = 400
MAX_SUMMARY_LINE_CHARS = 160

MAX_README_CHARS = 6000
MAX_ROUTE_README_CHARS = 6000
MAX_OPTIONS_CHARS = 300
MAX_PROPERTY_SETTERS = 40
MAX_PROPERTY_VALUE_CHARS = 200

MAX_CHANGELOG_ENTRIES = 5
MAX_CHANGELOG_EXCERPT_CHARS = 2500
#: The newest 200 release sections, which at this repo's pace is about two weeks (2026-09), so a
#: doctype last mentioned a month ago gets no excerpts. Older sections describe code that has
#: usually been rewritten since, and quoting them would plan against the past.
CHANGELOG_SECTIONS_SCANNED = 200

#: A basename shorter than this matches too much prose to be a useful search term:
#: ``pay`` or ``task`` appears in half the changelog.
MIN_TERM_CHARS = 5

#: What ``fit_to_budget`` caps a field list to, in turn, before giving up on fields.
FIELD_CAPS = (60, 30)

#: Layout, not data. A Table field stays: its options name the child doctype.
_LAYOUT_FIELDTYPES = frozenset(
	{"Section Break", "Column Break", "Tab Break", "Fold", "Heading", "HTML", "Button"}
)

_DESK_BASES = ("desk", "app")

#: Route words a doctype name follows: ``/app/Form/Item/…``, ``/desk/print/Sales Invoice/…``.
_DOCTYPE_PREFIX_WORDS = frozenset({"list", "form", "print", "tree"})

#: Route words that are not a doctype slug, some of which would otherwise resolve to one
#: (``workspace``, ``page`` and ``report`` are all DocTypes, and none is what the person was
#: looking at).
_NOT_A_DOCTYPE = frozenset(
	{
		"query-report",
		"view",
		"workspace",
		"workspaces",
		"page",
		"report",
		"dashboard-view",
		"user-profile",
		"setup-wizard",
		"build",
		"modules",
	}
)

#: ``/desk/<doctype>/view/<one of these>`` is a screen, not a record, and stays in the path.
#: Anything after it (a saved Kanban board's name, say) is somebody's data and does not.
_KNOWN_VIEWS = frozenset(
	{"list", "report", "kanban", "calendar", "gantt", "dashboard", "image", "tree", "map", "inbox"}
)

#: A web route segment we are willing to turn into a filename. Anything else is refused
#: rather than escaped: the path is what a browser sent.
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")

#: An absolute URL: a scheme followed by ``//``, or a scheme-relative ``//``. Only these are
#: handed to ``urlsplit``; anything else must already be a path.
_ABSOLUTE_URL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:)?//")

#: Doctype-level property setters that record layout, not behavior. ``field_order`` alone is
#: a JSON list of every fieldname and would crowd out the setters that matter.
_NOISE_PROPERTIES = frozenset({"field_order", "links_order", "actions_order", "states_order", "modified"})

_CHANGELOG_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\S+)[ \t]*$", re.M)
_HEADING = re.compile(r"^#{1,6} ")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


# ------------------------------------------------------------------------------ entry point


def build_anchors(context_url: str, context_doctype: str = "") -> dict[str, Any]:
	"""The Anchors object for one request's captured context. Never raises.

	``context_url`` is the stored ``Enhancement Request.context_url``, query string and all;
	it is read here and never sent. ``{}`` when nothing could be anchored or the build failed.
	"""
	try:
		return _build(context_url or "", context_doctype or "")
	except Exception:
		# Missing anchors cost accuracy; a raising build costs the whole breakdown.
		return {}


def _build(context_url: str, context_doctype: str) -> dict[str, Any]:
	package = frappe.get_app_path(APP)
	repo_root = os.path.dirname(package)
	path, kind = parse_path(context_url)

	route = None
	if kind == "web":
		route = _step(_web_route, package, context_url, path)
	elif kind == "desk":
		route = {"path": path, "kind": "desk"}

	doctypes = []
	name = _step(_resolve_doctype, context_doctype, context_url)
	if name:
		entry = _step(_doctype_entry, package, name)
		if entry:
			doctypes.append(entry)

	# A desk path with no doctype behind it (a workspace, a report) anchors nothing: the
	# path is already in `request.context.url`.
	if not doctypes and (route is None or route.get("kind") != "web"):
		return {}

	anchors: dict[str, Any] = {
		"repo": APP,
		"version": _step(_app_version) or "",
		"route": route,
		"doctypes": doctypes,
	}
	readme = _step(_readme, package, doctypes)
	if readme:
		anchors["readme"] = readme
	changelog = _step(_changelog, repo_root, route, doctypes)
	if changelog:
		anchors["changelog"] = changelog
	anchors["truncated"] = []
	return fit_to_budget(anchors, MAX_ANCHOR_CHARS)


def _step(fn, *args):
	"""Run one step; a step that fails contributes nothing."""
	try:
		return fn(*args)
	except Exception:
		return None


def _app_version() -> str:
	return str(frappe.get_attr(f"{APP}.__version__") or "")


# ------------------------------------------------------------------------------ paths


def _bare_path(url: str | None) -> str | None:
	"""The path of a stored ``context_url``: no scheme, host, query string or fragment.

	``None`` when the value is neither a path (it starts with ``/``) nor an absolute URL (a
	scheme and ``//``, or just ``//``). ``erp.example.com/desk/item`` is neither, and taken as a
	path its first segment would be the host; ``https:erp.example.com/desk`` likewise. The widget
	and the ``/feedback`` form both store a path, so only a hand-made value lands here, and it
	anchors nothing rather than being guessed at. Internal: never sent.
	"""
	text = str(url or "").strip()
	for mark in ("?", "#"):
		text = text.split(mark, 1)[0]
	text = text.strip()
	if _ABSOLUTE_URL.match(text):
		try:
			return urlsplit(text).path or "/"
		except ValueError:
			return None
	return text if text.startswith("/") else None


def _raw_segments(url: str | None) -> list[str]:
	"""Every segment of the path, query string and fragment gone. Internal: never sent."""
	return [segment for segment in (_bare_path(url) or "").split("/") if segment]


def parse_path(url: str | None) -> tuple[str, str | None]:
	"""``(path, kind)`` for a stored ``context_url``, reduced to the screen, never the record.

	No scheme, host, query string or fragment, ever: everything from the first ``?`` or ``#``
	goes. ``kind`` is ``"desk"`` for ``/desk``, ``/desk/…``, ``/app`` and ``/app/…``, otherwise
	``"web"``, and ``None`` with an empty path, or with a value that is neither a path nor an
	absolute URL (``erp.example.com/desk/item`` would otherwise send its host as the path).

	The path is then cut to what names the screen, because the rest of it is the record. The
	capture widget stores ``location.pathname`` and on a form that is
	``/desk/item/PUMP-001``: dropping the query string alone would still send the docname the
	payload no longer carries as a field. So a desk path keeps its doctype slug (after a
	``Form``/``List``/``print``/``tree`` word, the doctype that follows it) and a known
	``/view/<type>``; a web path keeps its first segment, so ``/feedback/request/ER-…`` is
	``/feedback``.
	"""
	bare = _bare_path(url)
	if not bare:
		# Empty, only a query string or fragment, or neither a path nor an absolute URL.
		return "", None
	segments = [segment for segment in bare.split("/") if segment]
	if not segments:
		# "/", or a bare origin: the home page.
		return "/", "web"

	if segments[0].lower() not in _DESK_BASES:
		return "/" + segments[0], "web"

	rest = segments[1:]
	keep = segments[:1]
	if rest:
		if rest[0].lower() in _DOCTYPE_PREFIX_WORDS:
			keep += rest[:2]
		else:
			keep.append(rest[0])
			if len(rest) >= 3 and rest[1].lower() == "view" and rest[2].lower() in _KNOWN_VIEWS:
				keep += rest[1:3]
	return "/" + "/".join(keep), "desk"


def desk_doctype_slug(url: str | None) -> str:
	"""The segment of a desk path that names a doctype, or ``""``.

	The first segment after ``/desk/`` or ``/app/``, stepping over a ``Form``/``List``/
	``print``/``tree`` word to the doctype that follows it.
	"""
	segments = _raw_segments(url)
	if len(segments) < 2 or segments[0].lower() not in _DESK_BASES:
		return ""
	slug = segments[1]
	if slug.lower() in _DOCTYPE_PREFIX_WORDS:
		return segments[2] if len(segments) > 2 else ""
	return slug


def desk_slug_to_candidates(slug: str | None) -> list[str]:
	"""DocType names a desk slug could be, most likely first. ``[]`` for a route word.

	Frappe's slug is the name lowercased with spaces as hyphens, so ``sales-invoice`` gives
	``sales invoice``, which the caller confirms against ``tabDocType`` (case-insensitive
	under the site collation). The decoded slug itself is the second candidate, for the
	old-style routes that carry the name verbatim (``/app/Form/Sales%20Invoice/…``).
	"""
	text = unquote(str(slug or "")).strip()
	if not text or text.lower() in _NOT_A_DOCTYPE or text.lower() in _DOCTYPE_PREFIX_WORDS:
		return []
	out: list[str] = []
	for candidate in (text.replace("-", " "), text):
		candidate = " ".join(candidate.split())
		if candidate and candidate not in out:
			out.append(candidate)
	return out


def route_rule_target(path: str, rules: list[dict[str, Any]] | None) -> str:
	"""The page a ``website_route_rules`` entry sends ``path`` to, or ``""``.

	``/feedback/<path:feedback_path>`` sends ``/feedback/request/ER-…`` to ``feedback``.
	"""
	for rule in rules or []:
		try:
			pattern = _rule_regex(str(rule.get("from_route") or ""))
			target = str(rule.get("to_route") or "").strip("/")
		except Exception:
			continue
		if pattern is not None and target and pattern.fullmatch(path):
			return target.split("/")[0]
	return ""


def _rule_regex(from_route: str) -> re.Pattern | None:
	if not from_route:
		return None
	parts = []
	for piece in re.split(r"(<[^>]+>)", from_route.rstrip("/")):
		if piece.startswith("<") and piece.endswith(">"):
			parts.append(".+" if piece.startswith("<path:") else "[^/]+")
		else:
			parts.append(re.escape(piece))
	return re.compile("".join(parts))


# ------------------------------------------------------------------------------ outlines


def outline_python(source: str, cap: int = MAX_OUTLINE_CHARS) -> str:
	"""A table of contents for a Python file, from ``ast``: ``""`` if it does not parse.

	The module docstring's first paragraph, then each top-level class with its bases and
	its methods, then the top-level functions, each as ``name(args)`` with the first line of
	its docstring and its decorators by name (``@frappe.whitelist`` is the one that matters:
	it is the difference between a helper and an HTTP endpoint).
	"""
	try:
		tree = ast.parse(source or "")
	except (SyntaxError, ValueError):
		return ""

	lines: list[str] = []
	doc = ast.get_docstring(tree) or ""
	first = " ".join(doc.strip().split("\n\n", 1)[0].split())
	if first:
		lines += [first[:MAX_DOCSTRING_CHARS], ""]

	for node in tree.body:
		if isinstance(node, ast.ClassDef):
			bases = ", ".join(_unparse(base) for base in node.bases)
			lines.append(f"class {node.name}({bases}):" if bases else f"class {node.name}:")
			for item in node.body:
				if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
					lines.append("    " + _signature(item))

	functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
	if functions:
		if lines and lines[-1] != "":
			lines.append("")
		lines += [_signature(node) for node in functions]

	return _cap_at_line("\n".join(lines).strip(), cap)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
	decorators = " ".join(
		"@" + _unparse(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list
	)
	prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
	text = f"{prefix}{node.name}({_arguments(node.args)})"
	if decorators:
		text = f"{decorators} {text}"
	summary = (ast.get_docstring(node) or "").strip().split("\n", 1)[0].strip()
	if summary:
		text += "  # " + summary[:MAX_SUMMARY_LINE_CHARS]
	return text


def _arguments(args: ast.arguments) -> str:
	"""Names, markers and short defaults; annotations are left out to keep lines short."""
	out: list[str] = []
	positional = [*args.posonlyargs, *args.args]
	defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
	for arg, default in zip(positional, defaults, strict=False):
		out.append(_with_default(arg.arg, default))
	if args.vararg:
		out.append("*" + args.vararg.arg)
	elif args.kwonlyargs:
		out.append("*")
	for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
		out.append(_with_default(arg.arg, default))
	if args.kwarg:
		out.append("**" + args.kwarg.arg)
	return ", ".join(out)


def _with_default(name: str, default: ast.expr | None) -> str:
	if default is None:
		return name
	value = _unparse(default)
	return f"{name}={value}" if len(value) <= 24 else f"{name}=…"


def _unparse(node: ast.AST) -> str:
	try:
		return ast.unparse(node)
	except Exception:
		return "?"


# ------------------------------------------------------------------------------ markdown


def _blocks(text: str) -> list[tuple[int, bool, str]]:
	"""Paragraphs and bullets as ``(indent, is_bullet, text)``; a code fence is never split.

	A bullet at any depth starts a block, and its wrapped lines stay with it.
	"""
	blocks: list[list[str]] = []
	current: list[str] = []
	fenced = False
	for line in (text or "").splitlines():
		stripped = line.strip()
		if stripped.startswith("```"):
			if not fenced and current:
				blocks.append(current)
				current = []
			current.append(line)
			fenced = not fenced
			continue
		if fenced:
			current.append(line)
			continue
		if not stripped:
			if current:
				blocks.append(current)
				current = []
			continue
		if _BULLET.match(line) and current:
			blocks.append(current)
			current = []
		current.append(line)
	if current:
		blocks.append(current)
	return [
		(len(block[0]) - len(block[0].lstrip()), bool(_BULLET.match(block[0])), "\n".join(block))
		for block in blocks
	]


def _matching_blocks(text: str, predicate) -> list[str]:
	"""The blocks of ``text`` that satisfy ``predicate``, each bullet with everything under it.

	A bullet that names the term keeps its nested bullets and paragraphs, because in this
	changelog the lead bullet names the thing ("**Enhancement Request fields:**") and the
	children say what changed. A nested bullet that matches on its own comes without its
	parent, so one line in a long list does not drag the list in.
	"""
	blocks = _blocks(text)
	out: list[str] = []
	index = 0
	while index < len(blocks):
		indent, bullet, body = blocks[index]
		if not predicate(body):
			index += 1
			continue
		end = index + 1
		if bullet:
			while end < len(blocks) and blocks[end][0] > indent:
				end += 1
		chunk = "\n".join(block[2] for block in blocks[index:end])
		out.append(textwrap.dedent(chunk).strip("\n"))
		index = end
	return out


def _markdown_sections(text: str) -> list[tuple[str, str]]:
	"""``(heading, body)`` for every heading, fence-aware; text before the first is ``("", …)``."""
	sections: list[tuple[str, list[str]]] = [("", [])]
	fenced = False
	for line in (text or "").splitlines():
		if line.strip().startswith("```"):
			fenced = not fenced
		if not fenced and _HEADING.match(line):
			sections.append((line.strip(), []))
		else:
			sections[-1][1].append(line)
	return [(heading, "\n".join(body)) for heading, body in sections if heading or "\n".join(body).strip()]


def _mentions_path(text: str, needle: str) -> bool:
	"""``needle`` as a path: ``/pay`` matches ``/pay`` and ``www/pay.py``, not ``/pay-card``."""
	return re.search(re.escape(needle) + r"(?![\w-])", text, re.I) is not None


def readme_sections(text: str, needle: str, cap: int = MAX_ROUTE_README_CHARS) -> str:
	"""The parts of a README that mention ``needle`` (a route path such as ``/kiosk``).

	A section whose heading names the path is about it and comes whole. Otherwise only the
	paragraphs, bullets and code blocks that name it come, under their heading: ``www/``'s
	README introduces every page in one list, and the kiosk's bullet is the useful line of
	that list, not the other eight.
	"""
	if not text or not needle:
		return ""
	out: list[str] = []
	for heading, body in _markdown_sections(text):
		if heading and _mentions_path(heading, needle):
			out.append(f"{heading}\n{body.strip()}".strip())
			continue
		matching = _matching_blocks(body, lambda block: _mentions_path(block, needle))
		if matching:
			out.append("\n\n".join(([heading] if heading else []) + matching))
	return _cap_at_line("\n\n".join(out), cap)


def changelog_sections(text: str) -> list[dict[str, str]]:
	"""Every ``## [x.y.z] - date`` section, newest first (file order), as version/date/body."""
	matches = list(_CHANGELOG_HEADING.finditer(text or ""))
	out = []
	for index, match in enumerate(matches):
		end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
		body = text[match.end() : end]
		# `[Unreleased]` and any other `## ` heading end a section too.
		stop = re.search(r"^## ", body, re.M)
		if stop:
			body = body[: stop.start()]
		out.append({"version": match.group(1), "date": match.group(2), "body": body.strip("\n")})
	return out


def changelog_excerpts(
	sections: list[dict[str, str]],
	terms: list[tuple[str, bool]],
	limit: int = MAX_CHANGELOG_ENTRIES,
) -> list[dict[str, str]]:
	"""The lines of recent releases that name a term: newest first, at most ``limit`` sections.

	``terms`` are ``(term, case_sensitive)`` pairs, each matched as a whole word with a plural
	allowed (``Items``, ``Item Group``, never ``playlistItems``). A doctype name is matched as
	written, because "Item" and "item" are different claims in prose; a path or file name is
	matched case-insensitively, and a route path (``/pay``) as a path, so ``/pay-card`` is not it.
	Only the newest ``CHANGELOG_SECTIONS_SCANNED`` sections are read, and from each only the
	paragraphs and bullets that match are kept, each excerpt capped at a line boundary.
	"""
	usable = [(term, bool(exact)) for term, exact in terms or [] if term]
	if not usable or limit <= 0:
		return []
	out = []
	for section in (sections or [])[:CHANGELOG_SECTIONS_SCANNED]:
		matching = _matching_blocks(section.get("body") or "", lambda block: _matches(block, usable))
		if not matching:
			continue
		out.append(
			{
				"version": section.get("version") or "",
				"date": section.get("date") or "",
				"excerpt": _cap_at_line("\n".join(matching), MAX_CHANGELOG_EXCERPT_CHARS),
			}
		)
		if len(out) >= limit:
			break
	return out


def _matches(block: str, terms: list[tuple[str, bool]]) -> bool:
	for term, exact in terms:
		if term.startswith("/") and not exact:
			if _mentions_path(block, term):
				return True
		elif _mentions_word(block, term, exact):
			return True
	return False


def _mentions_word(text: str, term: str, case_sensitive: bool) -> bool:
	"""``term`` as a whole word, a plural allowed: ``Item`` matches ``Items`` and ``Item Group``.

	Never inside a longer word. A bare substring test matched ``Item`` inside a YouTube
	``playlistItems.insert`` bullet, ``Note`` inside a ``### Noted`` heading and ``File`` inside
	``Filenames``, and quoted each as that doctype's history. ``(?:e?s)?`` rather than ``s?`` so
	``Address`` still finds ``Addresses``.
	"""
	pattern = r"(?<![\w])" + re.escape(term) + r"(?:e?s)?(?![\w])"
	return re.search(pattern, text, 0 if case_sensitive else re.I) is not None


def changelog_terms(route: dict[str, Any] | None, doctypes: list[dict[str, Any]]) -> list[tuple[str, bool]]:
	"""What a CHANGELOG line must name to be about this request.

	Per doctype: its name (as written), its folder name, its controller's path and basename.
	For a web route: the path and the controller's. A controller path is matched without the
	app folder in front (``www/kiosk.py``), which is how the changelog writes it, and which
	still matches the full path. Names shorter than ``MIN_TERM_CHARS`` are dropped.

	A case-insensitive term that is only a doctype's name lowercased is dropped too. For a
	one-word doctype the folder and the controller's basename are exactly that (``project``),
	and matched case-insensitively they undo the case rule: ``Project`` would match every
	"project" in prose, ``Customer`` "customer-provided items", ``Employee`` "<employee>". The
	name itself, matched as written, already covers the doctype.
	"""
	terms: list[tuple[str, bool]] = []
	lowered_names = {str(doctype.get("name") or "").lower() for doctype in doctypes or []}

	def add(term: str, exact: bool) -> None:
		if not term or (term, exact) in terms:
			return
		if not exact and term.lower() in lowered_names:
			return
		terms.append((term, exact))

	def add_file(path: str) -> None:
		if not path:
			return
		add(path.split("/", 1)[1] if "/" in path else path, False)
		base = os.path.splitext(os.path.basename(path))[0]
		if len(base) >= MIN_TERM_CHARS:
			add(base, False)

	for doctype in doctypes or []:
		name = doctype.get("name") or ""
		add(name, True)
		folder = name.replace(" ", "_").replace("-", "_").lower()
		if len(folder) >= MIN_TERM_CHARS:
			add(folder, False)
		add_file((doctype.get("controller") or {}).get("path") or "")

	if route and route.get("kind") == "web":
		add(route.get("path") or "", False)
		add_file(route.get("controller") or "")
	return terms


# ------------------------------------------------------------------------------ doctype facts


def project_field(df: Any) -> dict[str, Any]:
	"""One field as schema facts: the keys a planner reads, falsy ones left out.

	``fieldname`` and ``fieldtype`` always; ``label``, ``options`` (capped), ``reqd``,
	``read_only``, ``permlevel`` and ``custom`` only when set. Never a value: a DocField has
	a ``default`` but no value, and not even the default is sent.
	"""
	get = df.get if callable(getattr(df, "get", None)) else (lambda key: getattr(df, key, None))
	out: dict[str, Any] = {
		"fieldname": str(get("fieldname") or ""),
		"fieldtype": str(get("fieldtype") or ""),
	}
	label = get("label")
	if label:
		out["label"] = str(label)
	options = get("options")
	if options:
		out["options"] = str(options)[:MAX_OPTIONS_CHARS]
	for key in ("reqd", "read_only"):
		if _int(get(key)):
			out[key] = 1
	permlevel = _int(get("permlevel"))
	if permlevel:
		out["permlevel"] = permlevel
	if _int(get("is_custom_field")):
		out["custom"] = 1
	return out


def _int(value: Any) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _resolve_doctype(context_doctype: str, context_url: str) -> str:
	"""The doctype the request was filed from, confirmed to exist, or ``""``.

	``context_doctype`` first; then, for a desk path, its slug. A name that is not a DocType
	on this site resolves to nothing.
	"""
	candidates: list[str] = []
	if (context_doctype or "").strip():
		candidates.append(context_doctype.strip())
	for candidate in desk_slug_to_candidates(desk_doctype_slug(context_url)):
		if candidate not in candidates:
			candidates.append(candidate)
	for candidate in candidates:
		try:
			name = frappe.db.get_value("DocType", candidate, "name")
		except Exception:
			continue
		if name:
			return str(name)
	return ""


def _doctype_entry(package: str, name: str) -> dict[str, Any] | None:
	meta = frappe.get_meta(name)
	name = str(meta.get("name") or name)
	module = str(meta.get("module") or "")
	owner = _step(_module_app, module) or ""
	folder = frappe.scrub(name)

	entry: dict[str, Any] = {
		"name": name,
		"origin": "app" if owner == APP else (owner or "unknown"),
		"module": module,
	}
	if owner == APP and os.path.isdir(os.path.join(package, frappe.scrub(module), "doctype", folder)):
		entry["path"] = f"{APP}/{frappe.scrub(module)}/doctype/{folder}/"
	entry["is_submittable"] = 1 if _int(meta.get("is_submittable")) else 0
	entry["is_single"] = 1 if _int(meta.get("issingle")) else 0
	entry["is_child"] = 1 if _int(meta.get("istable")) else 0

	fields = []
	restricted = []
	for df in getattr(meta, "fields", None) or []:
		projected = project_field(df)
		if projected["fieldtype"] in _LAYOUT_FIELDTYPES or not projected["fieldname"]:
			continue
		fields.append(projected)
		if projected.get("permlevel") or projected["fieldtype"] == "Password":
			restricted.append(projected["fieldname"])
	entry["fields"] = fields
	entry["fields_total"] = len(fields)
	entry["restricted_fields"] = restricted

	controller = _step(_controller, owner, module, folder)
	if controller:
		entry["controller"] = controller
	customizations = _step(_customizations, name)
	if customizations:
		entry["customizations"] = customizations
	return entry


def _module_app(module: str) -> str:
	"""The app that owns a module: ``frappe.local.module_app``, then ``get_module_app``."""
	scrubbed = frappe.scrub(module)
	try:
		app = (getattr(frappe.local, "module_app", None) or {}).get(scrubbed)
		if app:
			return str(app)
	except Exception:
		pass
	from frappe.modules.utils import get_module_app

	return str(get_module_app(module) or "")


def _controller(owner: str, module: str, folder: str) -> dict[str, str] | None:
	if not owner:
		return None
	relative = f"{frappe.scrub(module)}/doctype/{folder}/{folder}.py"
	path = os.path.join(frappe.get_app_path(owner), *relative.split("/"))
	if not os.path.isfile(path):
		return None
	cap = MAX_OUTLINE_CHARS if owner == APP else MAX_CORE_OUTLINE_CHARS
	return {"path": f"{owner}/{relative}", "outline": outline_python(_read(path), cap)}


def _customizations(name: str) -> dict[str, Any]:
	"""What THIS app does to a doctype, whoever owns it: hooks, then property setters."""
	out: dict[str, Any] = {}

	events = _step(lambda: _app_hook("doc_events").get(name)) or {}
	if isinstance(events, dict):
		doc_events = {
			event: _as_list(handlers) for event, handlers in sorted(events.items()) if _as_list(handlers)
		}
		if doc_events:
			out["doc_events"] = doc_events

	scripts = []
	for hook in ("doctype_js", "doctype_list_js"):
		for script in _as_list(_step(lambda hook=hook: _app_hook(hook).get(name))):
			if script not in scripts:
				scripts.append(script)
	if scripts:
		out["doctype_js"] = scripts

	override = _as_list(_step(lambda: _app_hook("override_doctype_class").get(name)))
	if override:
		# Frappe imports the last one registered: base_document.import_controller.
		out["override_class"] = override[-1]

	setters = _step(_property_setters, name)
	if setters:
		out["property_setters"] = setters
	return out


def _app_hook(hook: str) -> dict[str, Any]:
	"""One of this app's own hooks.

	v16's ``get_hooks`` takes ``app_name`` (``frappe/__init__.py`` on ``version-16``). Where it
	does not, the merged hooks are filtered to entries that are plainly ours.
	"""
	try:
		value = frappe.get_hooks(hook, default={}, app_name=APP)
	except TypeError:
		value = _only_ours(frappe.get_hooks(hook, default={}))
	return value if isinstance(value, dict) else {}


def _only_ours(value: Any) -> Any:
	if isinstance(value, dict):
		return {key: _only_ours(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [item for item in value if isinstance(item, str) and item.startswith((f"{APP}.", "public/"))]
	if isinstance(value, str):
		return value if value.startswith((f"{APP}.", "public/")) else []
	return []


def _as_list(value: Any) -> list[str]:
	if not value:
		return []
	if isinstance(value, str):
		return [value]
	if isinstance(value, (list, tuple)):
		return [str(item) for item in value if item]
	return []


def _property_setters(name: str) -> list[dict[str, str]]:
	rows = frappe.get_all(
		"Property Setter",
		filters={"doc_type": name},
		fields=["field_name", "property", "value"],
		order_by="field_name asc, property asc, name asc",
		limit=500,
	)
	out = []
	for row in rows or []:
		field = str(row.get("field_name") or "")
		prop = str(row.get("property") or "")
		if not prop or (not field and prop in _NOISE_PROPERTIES):
			continue
		value = row.get("value")
		out.append(
			{
				"field_name": field,
				"property": prop,
				"value": ("" if value is None else str(value))[:MAX_PROPERTY_VALUE_CHARS],
			}
		)
	# Sorted here as well as in SQL: the collation and Python disagree on case and punctuation,
	# and the same deploy must give the same bytes.
	out.sort(key=lambda row: (row["field_name"], row["property"], row["value"]))
	return out[:MAX_PROPERTY_SETTERS]


# ------------------------------------------------------------------------------ web routes


def _web_route(package: str, context_url: str, path: str) -> dict[str, Any] | None:
	"""The ``www/`` page behind a web path, or ``None`` when this app does not serve it."""
	segments = _raw_segments(context_url)
	if not segments:
		return None
	segment = segments[0]
	rules = _step(lambda: frappe.get_hooks("website_route_rules", default=[], app_name=APP)) or []
	target = route_rule_target("/" + "/".join(segments), rules if isinstance(rules, list) else [])
	if target:
		segment = target
	if not _SEGMENT.match(segment):
		return None

	www = os.path.join(package, "www")
	# Frappe imports only an underscored controller (CLAUDE.md: a hyphenated one never runs);
	# a template keeps the route's own spelling.
	underscored = segment.replace("-", "_")
	controller = _first_file(www, [underscored, segment], ".py")
	template = _first_file(www, [segment, underscored], ".html")
	if not controller and not template:
		return None

	route: dict[str, Any] = {"path": path, "kind": "web"}
	if controller:
		route["controller"] = f"{APP}/www/{controller}"
	if template:
		route["template"] = f"{APP}/www/{template}"
	if controller:
		outline = outline_python(_read(os.path.join(www, controller)), MAX_OUTLINE_CHARS)
		if outline:
			route["outline"] = outline
	readme = readme_sections(_read(os.path.join(www, "README.md")), "/" + segment, MAX_ROUTE_README_CHARS)
	if readme:
		route["readme"] = readme
	return route


def _first_file(folder: str, stems: list[str], suffix: str) -> str:
	for stem in dict.fromkeys(stems):
		if os.path.isfile(os.path.join(folder, stem + suffix)):
			return stem + suffix
	return ""


# ------------------------------------------------------------------------------ readme + changelog


def _readme(package: str, doctypes: list[dict[str, Any]]) -> dict[str, str] | None:
	"""The README a contributor would read for this doctype.

	An app doctype: its module's. A core doctype this app hooks: the README of the module
	that owns the first handler that has one. A web route has none here; its ``www/`` README
	sections are already in ``route.readme``.
	"""
	for doctype in doctypes:
		folders: list[str] = []
		if doctype.get("origin") == "app":
			folders.append(frappe.scrub(doctype.get("module") or ""))
		else:
			for handlers in ((doctype.get("customizations") or {}).get("doc_events") or {}).values():
				for handler in handlers:
					parts = handler.split(".")
					if len(parts) > 2 and parts[0] == APP and parts[1] not in folders:
						folders.append(parts[1])
		for folder in folders:
			if not folder or not _SEGMENT.match(folder):
				continue
			text = _read(os.path.join(package, folder, "README.md"))
			if text:
				return {"path": f"{APP}/{folder}/README.md", "text": _cap_at_line(text, MAX_README_CHARS)}
	return None


def _changelog(repo_root: str, route: dict[str, Any] | None, doctypes: list[dict[str, Any]]) -> list:
	terms = changelog_terms(route, doctypes)
	if not terms:
		return []
	text = _read(os.path.join(repo_root, "CHANGELOG.md"))
	return changelog_excerpts(changelog_sections(text), terms, MAX_CHANGELOG_ENTRIES)


# ------------------------------------------------------------------------------ budget


def serialized_size(value: Any) -> int:
	"""The size the cap is defined on: compact JSON, non-ASCII kept as characters."""
	return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def fit_to_budget(anchors: dict[str, Any], max_chars: int = MAX_ANCHOR_CHARS) -> dict[str, Any]:
	"""Cut ``anchors`` until it serializes to at most ``max_chars``, then set ``chars``.

	The order is fixed, least useful first, and each step is recorded once in ``truncated``
	so the reader knows a list is partial: the oldest changelog entries; the README (halved,
	then dropped); every outline, halved; the field lists, to 60 then 30 (``fields_total``
	keeps the real count); property setters; the route's README. If all of that is not
	enough, outlines and field lists go entirely, and past that only the header is left,
	so the cap holds for any input.
	"""
	if not anchors:
		return anchors
	truncated = anchors.setdefault("truncated", [])
	# The widest `chars` can be, so every check below is conservative.
	anchors["chars"] = max_chars

	def over() -> bool:
		return serialized_size(anchors) > max_chars

	def note(step: str) -> None:
		if step not in truncated:
			truncated.append(step)

	doctypes = [d for d in anchors.get("doctypes") or [] if isinstance(d, dict)]
	route = anchors.get("route") if isinstance(anchors.get("route"), dict) else None

	# 1. The oldest changelog entries.
	changelog = anchors.get("changelog")
	if isinstance(changelog, list):
		while over() and changelog:
			changelog.pop()
			note("changelog")
		if not changelog:
			anchors.pop("changelog", None)

	# 2. The README: halved, then dropped.
	readme = anchors.get("readme")
	if over() and isinstance(readme, dict) and readme.get("text"):
		readme["text"] = _halve(readme["text"])
		note("readme")
		if over():
			anchors.pop("readme", None)

	# 3. Every outline, halved.
	holders = ([route] if route else []) + [d.get("controller") for d in doctypes]
	holders = [h for h in holders if isinstance(h, dict) and h.get("outline")]
	if over() and holders:
		for holder in holders:
			holder["outline"] = _halve(holder["outline"])
		note("outlines")

	# 4. The field lists, to 60 and then 30.
	for cap in FIELD_CAPS:
		if not over():
			break
		for doctype in doctypes:
			if len(doctype.get("fields") or []) > cap:
				doctype["fields"] = doctype["fields"][:cap]
				note("fields")

	# 5. Property setters. A `customizations` they leave empty goes with them: `{}` reads as
	# "this app does nothing to the doctype", which is the opposite of what was cut.
	if over():
		for doctype in doctypes:
			customizations = doctype.get("customizations")
			if isinstance(customizations, dict) and customizations.pop("property_setters", None):
				note("property_setters")
				if not customizations:
					doctype.pop("customizations", None)

	# 6. The route's README.
	if over() and route and route.pop("readme", None):
		note("route_readme")

	# Past the six steps: so the cap is a guarantee, not a hope.
	if over() and holders:
		for holder in holders:
			holder.pop("outline", None)
		note("outlines")
	if over() and any("fields" in doctype for doctype in doctypes):
		for doctype in doctypes:
			doctype.pop("fields", None)
		note("fields")
	if over():
		note("everything")
		for key in list(anchors):
			if key not in ("repo", "version", "truncated", "chars"):
				anchors.pop(key)

	# `chars` is part of what it measures, so settle it: at most two passes.
	for _ in range(3):
		size = serialized_size(anchors)
		if anchors["chars"] == size:
			break
		anchors["chars"] = size
	return anchors


def _halve(text: str) -> str:
	return _cap_at_line(text, len(text) // 2)


# ------------------------------------------------------------------------------ files


def _read(path: str) -> str:
	try:
		with open(path, encoding="utf-8") as handle:
			return handle.read()
	except Exception:
		return ""


def _cap_at_line(text: str, limit: int) -> str:
	"""``text`` cut to ``limit`` characters at a line boundary rather than mid-sentence."""
	if len(text) <= limit:
		return text
	cut = text.rfind("\n", 0, limit)
	return text[: cut if cut > 0 else limit].rstrip()
