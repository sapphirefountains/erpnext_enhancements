# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A map of this app's own source, for a model planning work inside it.

Answers the complaint that started this: the breakdown was written **blind**. Triton had no
access to either codebase — no GitHub integration, no filesystem, nothing in its RAG corpus —
so a proposed task could name a module that does not exist, put a portal page in ``api/``, or
describe work in a module whose whole point is something else. It produced plausible tasks
rather than accurate ones.

--------------------------------------------------------------------------------------
What is sent, and why it is this and not the code
--------------------------------------------------------------------------------------

Not the source. A repository of ~94k lines does not fit in a prompt, and a model that has
read three files at random is worse off than one that has read none — it will anchor on them.

What goes instead is **what a human contributor reads first**: the module map with its curated
one-line purpose per module, the layout of the packages where new code actually lands
(``api/``, ``www/``, ``patches/``), and the conventions from ``CLAUDE.md``.
This repo's whole documentation philosophy is that every module documents itself next to its
code, which makes that material unusually good and unusually current — it is the same material
that makes a new contributor productive, and it is the material a task description needs in
order to name a real file.

--------------------------------------------------------------------------------------
Read from the installed app, not from a repository
--------------------------------------------------------------------------------------

``frappe.get_app_path`` points at the source that is **actually running on this bench**. So
the map describes what is deployed rather than what some checkout says, and it cannot drift:
a module added in a release appears in the map of that release and not before.

Cached against the deploy version, because that is exactly when it changes.

Everything is bounded and nothing raises. A prompt that grows with the repository eventually
crowds out the request it is supposed to be reasoning about, and a map that fails to build
must degrade to a smaller prompt rather than to a failed breakdown.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import os
import re
from typing import Any

import frappe

#: Packages where new code actually lands, listed file-by-file. Everything else is described
#: at module level only — a model does not need the 40 files inside `sapphire_maintenance` to
#: know that maintenance work goes there.
_DETAILED_DIRS = ("api", "www", "patches", "utils", "scripts")

_CODE_SUFFIXES = (".py", ".js", ".html", ".css")

#: Caps. The map is a *frame* for the request, not the subject of the prompt.
MAX_FILES_PER_DIR = 60
MAX_CONVENTION_CHARS = 6000
MAX_PURPOSE_CHARS = 240
MAX_DOCTYPES_PER_MODULE = 40


def build_codemap() -> dict[str, Any]:
	"""The map, cached per deploy. Never raises; returns ``{}`` if it cannot be built."""
	try:
		from erpnext_enhancements.utils.deploy import get_deploy_version

		key = f"product_feedback:codemap:{get_deploy_version()}"
	except Exception:
		key = "product_feedback:codemap:unknown"

	try:
		cached = frappe.cache().get_value(key)
		if cached:
			return cached
	except Exception:
		cached = None

	try:
		built = _build()
	except Exception:
		# A missing map costs accuracy; a raising one costs the whole breakdown.
		return {}

	try:
		frappe.cache().set_value(key, built, expires_in_sec=24 * 3600)
	except Exception:
		pass
	return built


def _build() -> dict[str, Any]:
	package = frappe.get_app_path("erpnext_enhancements")
	repo_root = os.path.dirname(package)

	return {
		"repo": "erpnext_enhancements",
		"language": "Python (Frappe v16 app) + vanilla JS/CSS browser assets",
		"version": frappe.get_attr("erpnext_enhancements.__version__"),
		"layout": (
			"erpnext_enhancements/<module>/doctype/<doctype>/ holds a DocType's .json schema, "
			".py controller and optional .js form script. Whitelisted HTTP endpoints live in "
			"erpnext_enhancements/api/<area>.py. Standalone portal pages are a template plus an "
			"underscored controller in erpnext_enhancements/www/. One-time migrations are "
			"erpnext_enhancements/patches/*.py plus a line in patches.txt. Browser assets are "
			"erpnext_enhancements/public/{js,css}/. Every customization is registered in "
			"erpnext_enhancements/hooks.py."
		),
		"environment": _environment(),
		"modules": _modules(package, repo_root),
		"doctypes": _doctypes(package),
		"packages": _detailed_dirs(package),
		"conventions": _conventions(repo_root),
	}


def _environment() -> dict[str, Any]:
	"""What this actually runs on, version numbers included.

	A model planning ERPNext work needs to know it is ERPNext **v16** and not v15 or v17 —
	the differences are load-bearing (v17 deprecates the legacy ``?cmd=`` route, adds ``QUERY``
	to ``SAFE_HTTP_METHODS``, and moves half of ``permissions.py``), and a task written against
	the wrong major is a task somebody has to throw away. Read from the installed apps rather
	than stated, so it cannot be wrong.

	MariaDB's version is included because it decided a real architectural question here once:
	10.11 has no ``VECTOR`` type, which is why chat embeddings are not stored in the database.
	"""
	env: dict[str, Any] = {}
	try:
		apps = frappe.get_installed_apps()
	except Exception:
		apps = []

	versions = {}
	for app in apps:
		try:
			versions[app] = frappe.get_attr(f"{app}.__version__")
		except Exception:
			continue
	env["apps"] = versions

	try:
		import sys

		env["python"] = f"{sys.version_info.major}.{sys.version_info.minor}"
	except Exception:
		pass

	try:
		env["database"] = frappe.db.sql("select version()")[0][0]
	except Exception:
		pass

	env["note"] = (
		"Frappe/ERPNext v16. Do not write tasks against v15 or v17 behaviour — this repo has "
		"already had two design documents cite v17 line numbers as v16 fact."
	)
	return env


def _doctypes(package: str) -> dict[str, list[str]]:
	"""This app's own DocTypes, grouped by module.

	The single most useful fact for planning ERPNext work, and the one a file listing does not
	give: whether the thing already exists. A request for "a report of items needed for a
	project" is plannable only if you know ``Material Request`` and ``Purchase Order`` are
	there to report over, and that ``Water Feature Design`` is a custom doctype while
	``Purchase Receipt`` is stock ERPNext.

	Read from the filesystem rather than from ``tabDocType`` on purpose: this is a map of the
	*app*, and a site-level Custom DocType somebody made in the desk is not part of it.
	"""
	out: dict[str, list[str]] = {}
	try:
		modules = [
			line.strip()
			for line in open(os.path.join(package, "modules.txt"), encoding="utf-8")
			if line.strip()
		]
	except Exception:
		return {}

	for module in modules:
		folder = os.path.join(package, frappe.scrub(module), "doctype")
		if not os.path.isdir(folder):
			continue
		try:
			names = sorted(
				entry.replace("_", " ").title()
				for entry in os.listdir(folder)
				if os.path.isdir(os.path.join(folder, entry)) and not entry.startswith("_")
			)
		except Exception:
			continue
		if names:
			out[module] = names[:MAX_DOCTYPES_PER_MODULE]
	return out


def _modules(package: str, repo_root: str) -> list[dict[str, str]]:
	"""Every module in ``modules.txt``, with one line of what it covers.

	**The purpose comes from the root README's module-map table**, which carries a curated
	one-liner for every module and is the thing a new contributor reads. The per-module
	README's H1 is the fallback, and it is only a fallback because the convention is not
	uniform: newer modules write ``# `chat/` — what it covers`` while most older ones write
	just ``# Chat``, which as a purpose line says nothing. Taking the module name and calling
	it a description is worse than leaving it blank — it reads like information.
	"""
	try:
		with open(os.path.join(package, "modules.txt"), encoding="utf-8") as handle:
			names = [line.strip() for line in handle if line.strip()]
	except Exception:
		return []

	curated = _purposes_from_module_map(repo_root)

	out = []
	for name in names:
		folder = frappe.scrub(name)
		purpose = curated.get(folder) or _readme_purpose(os.path.join(package, folder, "README.md"))
		out.append(
			{
				"module": name,
				"folder": f"erpnext_enhancements/{folder}/",
				"purpose": purpose[:MAX_PURPOSE_CHARS],
			}
		)
	return out


#: `| **Enhancements Core** (`enhancements_core/`) | Catch-all: … | [README](…) |`
_MODULE_ROW = re.compile(
	r"^\|\s*\*\*(?P<name>[^*|]+)\*\*\s*\(\s*`?(?P<folder>[A-Za-z0-9_]+)/?`?\s*\)\s*\|(?P<purpose>[^|]*)\|"
)


def _purposes_from_module_map(repo_root: str) -> dict[str, str]:
	"""``folder -> what it covers``, parsed out of the root README's module map."""
	try:
		with open(os.path.join(repo_root, "README.md"), encoding="utf-8") as handle:
			text = handle.read()
	except Exception:
		return {}

	out: dict[str, str] = {}
	for line in text.splitlines():
		match = _MODULE_ROW.match(line.strip())
		if match:
			out[match.group("folder")] = _plain(match.group("purpose"))
	return out


def _readme_purpose(path: str) -> str:
	"""The text after the em-dash in a README's H1. Empty when the H1 is just a name."""
	try:
		with open(path, encoding="utf-8") as handle:
			for line in handle:
				line = line.strip()
				if not line.startswith("# "):
					continue
				heading = line[2:].strip()
				for dash in ("—", " — ", " - ", "–"):
					if dash in heading:
						return _plain(heading.split(dash, 1)[1])
				# An H1 that is only the module's own name is not a description.
				return ""
	except Exception:
		pass
	return ""


def _plain(markdown: str) -> str:
	"""Markdown reduced to prose: links become their text, emphasis is dropped."""
	text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", markdown or "")
	text = text.replace("**", "").replace("`", "")
	return re.sub(r"\s+", " ", text).strip()


def _detailed_dirs(package: str) -> dict[str, list[str]]:
	"""File listings for the packages where new code actually lands."""
	out: dict[str, list[str]] = {}
	for name in _DETAILED_DIRS:
		# `scripts/` is at the repo root, the rest are inside the package.
		base = os.path.join(package, name)
		prefix = f"erpnext_enhancements/{name}/"
		if not os.path.isdir(base):
			base = os.path.join(os.path.dirname(package), name)
			prefix = f"{name}/"
		if not os.path.isdir(base):
			continue
		try:
			files = sorted(
				entry
				for entry in os.listdir(base)
				if entry.endswith(_CODE_SUFFIXES) and not entry.startswith("_")
			)
		except Exception:
			continue
		if not files:
			continue
		out[prefix] = [prefix + entry for entry in files[:MAX_FILES_PER_DIR]]
	return out


def _conventions(repo_root: str) -> str:
	"""The Gotchas/Conventions half of ``CLAUDE.md``.

	That file exists to stop a contributor rediscovering expensive things, which is the same
	job it does here. Only the second half is sent — the first is orientation a model does
	not need, and the caps matter more than the completeness.
	"""
	try:
		with open(os.path.join(repo_root, "CLAUDE.md"), encoding="utf-8") as handle:
			text = handle.read()
	except Exception:
		return ""

	marker = "## Gotchas"
	index = text.find(marker)
	if index == -1:
		return text[:MAX_CONVENTION_CHARS]
	return text[index : index + MAX_CONVENTION_CHARS]
