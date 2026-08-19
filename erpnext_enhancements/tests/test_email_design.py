# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards for the email design system (``docs/email-design-system.md``).

Bench-free, and unusually it installs **no frappe stub at all**. It does not need
one: ``templates/emails/_components.html`` is forbidden from referencing
``frappe``, so the macros render under a vanilla ``jinja2.Environment`` whose
``FileSystemLoader`` resolves ``erpnext_enhancements/templates/...`` paths the
same way frappe's ``PrefixLoader`` does. That property is itself asserted below —
if somebody adds a ``get_url`` to a macro, this suite is what says no.

pytest, not unittest: the assertions are naturally parametrised over the template
files and the ``sendmail`` call sites, and ``python -m unittest`` silently
collects nothing from ``def test_*`` functions — the trap that left the
QuickBooks suite running nowhere for weeks. It therefore belongs on a
``python -m pytest`` step in ci.yml, never on a unittest module list.

The cases here are the ones where a mistake is expensive and silent: an email
that renders as a narrow centred box again, a macro that stops escaping, or a
colour that fails contrast in a client nobody on the team uses.
"""

import os
import re

import pytest
from jinja2 import Environment, FileSystemLoader

APP = "erpnext_enhancements"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EMAIL_DIR = os.path.join(REPO_ROOT, APP, "templates", "emails")

SHELL_REL = f"{APP}/templates/emails/_shell.html"
COMPONENTS_REL = f"{APP}/templates/emails/_components.html"

# Colours that must never reappear. The first is the app's own accessibility
# rule (scripts/test_chat_source_rules.js bans #00a0dd as text, 2.97:1); the next
# two are what the 13 hand-written Notification bodies used and both fail AA;
# the last is an off-brand Tailwind blue that reached the contract invite.
BANNED_HEX = {
	"#00a0dd": "brand blue may never carry text — 2.97:1",
	"#1e9e5a": "old CTA green — 3.52:1 with white text",
	"#00a1de": "old link blue — 2.90:1",
	"#2563eb": "off-brand Tailwind blue",
}

# Shrinks by phase; empty means every sender is on the design system.
UNMIGRATED_SENDERS: set[str] = set()

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def _strip_comments(src):
	"""Source with Jinja comments removed.

	The scanning assertions must read markup, not prose. Both of these files
	*document* the things they are forbidden to do — "frappe's Jinja env has NO
	autoescape", "four senders historically used <pre>" — and a naive substring
	search flags the explanation along with the offence.
	"""
	return re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)


def _ids(pairs):
	"""Path-only test ids. Without this, pytest uses the file *source* as the id."""
	return [rel for rel, _src in pairs]


def _read(*parts):
	with open(os.path.join(*parts), encoding="utf-8") as handle:
		return handle.read()


def _env():
	return Environment(loader=FileSystemLoader(REPO_ROOT), autoescape=False)


def _template_files():
	"""Every email template on disk, as (relative_path, source)."""
	found = []
	for root, _dirs, files in os.walk(EMAIL_DIR):
		for name in sorted(files):
			if name.endswith(".html"):
				path = os.path.join(root, name)
				found.append((os.path.relpath(path, REPO_ROOT).replace("\\", "/"), _read(path)))
	return found


def _body_templates():
	"""The message-body templates — everything except the shell and the macros."""
	return [(rel, src) for rel, src in _template_files() if not os.path.basename(rel).startswith("_")]


def _python_files():
	for root, dirs, files in os.walk(os.path.join(REPO_ROOT, APP)):
		dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__", "tests"}]
		for name in sorted(files):
			if name.endswith(".py"):
				path = os.path.join(root, name)
				yield os.path.relpath(path, REPO_ROOT).replace("\\", "/"), _read(path)


def _sendmail_sites():
	"""``(relpath, source)`` for every module that calls ``frappe.sendmail``."""
	return [(rel, src) for rel, src in _python_files() if "frappe.sendmail(" in src]


# ---------------------------------------------------------------------------
# The scan must not silently stop scanning. A test that quietly matches nothing
# is worse than no test; scripts/test_chat_source_rules.js takes the same
# precaution for the same reason.


def test_the_scan_still_finds_things():
	bodies = _body_templates()
	senders = _sendmail_sites()
	assert len(bodies) >= 12, f"expected 12+ body templates, found {len(bodies)} — has the scan broken?"
	assert len(senders) >= 15, f"expected 15+ sendmail modules, found {len(senders)} — has the scan broken?"
	assert os.path.isfile(os.path.join(EMAIL_DIR, "_shell.html"))
	assert os.path.isfile(os.path.join(EMAIL_DIR, "_components.html"))


# ---------------------------------------------------------------------------
# Structure — the centred box does not come back.


def test_shell_is_fluid_and_capped():
	shell = _strip_comments(_read(EMAIL_DIR, "_shell.html"))
	assert 'width="100%"' in shell, "the shell must be fluid"
	assert "max-width:840px" in shell, "the measure cap moved without updating this test"
	assert "[if mso]" in shell, "Outlook ignores max-width; the ghost table is what pins it"
	assert shell.count("[if mso]") == 2, "the ghost table must be opened and closed"
	assert "<html" not in shell.lower(), "frappe's standard.html supplies the document"
	assert "<body" not in shell.lower()


ALL_TEMPLATES = _template_files()
BODY_TEMPLATES = _body_templates()
SENDERS = _sendmail_sites()


@pytest.mark.parametrize("rel,src", BODY_TEMPLATES, ids=_ids(BODY_TEMPLATES))
def test_no_body_template_reimposes_a_container(rel, src):
	"""The regression guard for the narrow centred card.

	Body templates are fragments. The chrome belongs to the shell, which is
	reached through ``email_style.wrap()`` — never re-declared per template.
	"""
	markup = _strip_comments(src)
	assert "max-width" not in markup, f"{rel} re-imposes a width; the shell owns the measure"
	assert "margin:0 auto" not in markup.replace(" ", ""), f"{rel} centres a container"
	assert "<html" not in markup.lower(), f"{rel} must be a fragment"
	assert "<!doctype" not in markup.lower(), f"{rel} must be a fragment"


@pytest.mark.parametrize("rel,src", ALL_TEMPLATES, ids=_ids(ALL_TEMPLATES))
def test_every_template_compiles(rel, src):
	_env().get_template(rel)


# ---------------------------------------------------------------------------
# The macros are frappe-free, which is what makes this suite stub-free.


def test_components_never_reach_for_frappe():
	src = _strip_comments(_read(EMAIL_DIR, "_components.html"))
	assert "frappe" not in src, (
		"_components.html may not reference frappe — translated strings and "
		"absolute URLs arrive as arguments. See its header comment."
	)


# ---------------------------------------------------------------------------
# Colour. One source of truth, and nothing that fails contrast.


def _declared_palette():
	return {h.lower() for h in HEX.findall(_read(REPO_ROOT, APP, "email_style.py"))}


@pytest.mark.parametrize("rel", [SHELL_REL, COMPONENTS_REL])
def test_every_hex_is_declared_in_email_style(rel):
	declared = _declared_palette()
	used = {h.lower() for h in HEX.findall(_read(REPO_ROOT, *rel.split("/")))}
	undeclared = used - declared - {"#ffffff"}
	assert not undeclared, (
		f"{rel} uses colours not declared in email_style.py: {sorted(undeclared)}. "
		"Add them there so the palette has one source of truth."
	)


@pytest.mark.parametrize("rel,src", ALL_TEMPLATES, ids=_ids(ALL_TEMPLATES))
def test_no_banned_colour_in_templates(rel, src):
	lowered = src.lower()
	for hex_value, why in BANNED_HEX.items():
		assert hex_value not in lowered, f"{rel} uses {hex_value}: {why}"


def test_palette_meets_wcag_aa_on_white():
	"""Contrast computed, not asserted from a comment.

	The two colours this design replaced both failed, and the app already gates
	on this rule for the chat surface. Every value that carries text is checked
	so a future palette edit is caught rather than trusted.
	"""

	def luminance(hex_value):
		channels = []
		for i in (1, 3, 5):
			c = int(hex_value[i : i + 2], 16) / 255
			channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
		r, g, b = channels
		return 0.2126 * r + 0.7152 * g + 0.0722 * b

	def ratio(hex_value):
		lighter, darker = max(luminance(hex_value), 1.0), min(luminance(hex_value), 1.0)
		return (lighter + 0.05) / (darker + 0.05)

	src = _read(REPO_ROOT, APP, "email_style.py")
	for name in ("INK", "BODY_INK", "MUTED", "ACCENT", "SUCCESS", "WARNING", "DANGER"):
		value = re.search(rf'^{name} = "(#[0-9a-fA-F]{{6}})"', src, re.MULTILINE).group(1)
		assert ratio(value) >= 4.5, f"{name} {value} is {ratio(value):.2f}:1 on white — below AA"

	for banned in ("#1E9E5A", "#00A1DE", "#00a0dd"):
		assert ratio(banned) < 4.5, f"{banned} now passes AA — the ban may be stale"


# ---------------------------------------------------------------------------
# Escaping. frappe's Jinja env has no autoescape, so every macro escapes its own
# text arguments — and must escape them exactly once.


@pytest.fixture(scope="module")
def macros():
	return _env().get_template(COMPONENTS_REL).make_module({})


@pytest.mark.parametrize("name", ["h", "p", "prose", "code"])
def test_text_macros_escape_their_argument(macros, name):
	out = str(getattr(macros, name)("<script>alert(1)</script>"))
	assert "<script>" not in out
	assert "&lt;script&gt;" in out


def test_escaping_happens_exactly_once(macros):
	"""Double-escaping is the failure this suite exists to catch twice over.

	It is not a safety bug, so no assertion about ``<script>`` finds it — it just
	renders `Smith & Sons` as `Smith &amp; Sons` in the reader's inbox.
	"""
	assert "&amp; Sons" in str(macros.p("Smith & Sons"))
	assert "&amp;amp;" not in str(macros.p("Smith & Sons"))
	assert "&amp;amp;" not in str(macros.kv([("Client", "Smith & Sons")]))
	assert "&amp;amp;" not in str(macros.table(["A"], [["Smith & Sons"]]))


def test_structural_macros_escape_cells(macros):
	assert "&lt;b&gt;" in str(macros.kv([("L", "<b>v</b>")]))
	assert "&lt;b&gt;" in str(macros.table(["H"], [["<b>c</b>"]]))
	assert "&lt;b&gt;" in str(macros.bullets(["<b>x</b>"]))
	assert "&lt;b&gt;" in str(macros.callout("<b>x</b>", "warning"))


def test_button_does_not_escape_its_url(macros):
	"""An entity-escaped get_url() is a broken link — the one thing that must
	pass through raw. Guarded at ``templates/emails/...contract_signature_invite``
	in the old code by a comment; here by a test."""
	url = "https://erp.example.com/app/task?x=1&y=2"
	out = str(macros.button(url, "Open"))
	assert f'href="{url}"' in out, "the URL was escaped or altered"
	assert "&amp;y=2" not in out


def test_button_label_is_still_escaped(macros):
	assert "&lt;i&gt;" in str(macros.button("https://x.test", "<i>Go</i>"))


# ---------------------------------------------------------------------------
# The body cell is `class="ee-pad ee-md"`, and the shell styles the markdown body
# through DESCENDANT selectors — `.ee-md table{width:100%}` and
# `.ee-md td{border-bottom:…;padding:9px 10px}` — because md_to_html() emits tags
# with no attributes to style inline. Premailer inlines those onto every table and
# td in the body, the component macros' own included.
#
# So a macro that omits `width` on a table, or `padding`/`border` on a td, does not
# get the browser default. It gets the markdown body's. That is how the CTA ended up
# a 772x60 coloured bar containing a 212x41 link: 19% of the button was the button,
# and clicking the colour did nothing (v1.331.2).


def _tags(html, tag):
	for m in re.finditer(rf"<{tag}\b[^>]*>", html):
		style = re.search(r'style="([^"]*)"', m.group(0))
		yield m.group(0), (style.group(1) if style else "")


def test_every_macro_td_declares_padding_and_border():
	"""Declaring only one of the two is the trap: `links` set padding and still
	inherited a divider rule under every row."""
	body = _read(EMAIL_DIR, "_components.html")
	body = re.sub(r"\{#.*?#\}", "", body, flags=re.S)  # drop the doc comments
	for tag, style in _tags(body, "td"):
		assert "padding" in style, f"td inherits .ee-md padding: {tag}"
		assert re.search(r"(?<!-)border\s*:", style) or "border-bottom" in style, (
			f"td inherits the .ee-md divider rule: {tag}"
		)


def test_every_macro_table_declares_its_width():
	body = _read(EMAIL_DIR, "_components.html")
	body = re.sub(r"\{#.*?#\}", "", body, flags=re.S)
	for tag, style in _tags(body, "table"):
		assert "width" in style, f"table inherits .ee-md width:100%: {tag}"


def test_the_whole_button_is_the_link(macros):
	"""The coloured pill and the anchor must be the same box.

	Verified in a browser against the premailer output of a real sent message:
	with these three declarations the anchor is 100% of the cell, 0 dead pixels on
	every edge; without them it was 19%.
	"""
	out = str(macros.button("https://x.test", "Open the opportunity"))
	table = next(s for _, s in _tags(out, "table"))
	td = next(s for _, s in _tags(out, "td"))
	a = next(s for _, s in _tags(out, "a"))

	assert "width:auto" in table, "the cell stretches the full measure, the anchor does not"
	assert "padding:0" in td, "padded cell = coloured space outside the anchor"
	assert "border:0" in td

	# The padding belongs to the anchor: its own box is what the client makes
	# clickable. Moving it to the td would restore the dead border exactly.
	assert "padding:13px 30px" in a
	assert "display:inline-block" in a


def test_the_button_still_fills_for_outlook():
	"""Word ignores inline-block, so without `mso-padding-alt` the `padding:0` above
	would shrink the fill to the bare text there. Only the label is the hit target in
	Outlook either way — that needs <v:roundrect> and a hard-coded width."""
	body = _read(EMAIL_DIR, "_components.html")
	assert "mso-padding-alt:13px 30px" in body


# ---------------------------------------------------------------------------
# Responsiveness is a progressive enhancement, never a dependency.


def test_media_selectors_are_namespaced():
	shell = _read(EMAIL_DIR, "_shell.html")
	block = shell[shell.index("@media") : shell.index("</style>")]
	selectors = re.findall(r"^\s*([.#][\w\-.,\s]+)\s*\{", block, re.MULTILINE)
	for group in selectors:
		for selector in group.split(","):
			selector = selector.strip()
			if selector:
				assert selector.startswith(".ee-"), f"un-namespaced selector in @media: {selector}"


@pytest.mark.parametrize("rel,src", ALL_TEMPLATES, ids=_ids(ALL_TEMPLATES))
def test_pre_always_wraps(rel, src):
	"""frappe's .body-table is table-layout:fixed, so one long unbroken token —
	a traceback path, a checkout URL — pushes the whole email sideways."""
	for match in re.finditer(r"<pre\b[^>]*>", _strip_comments(src)):
		assert "pre-wrap" in match.group(0), f"{rel} has a <pre> that cannot wrap"


def test_layout_survives_without_the_style_block(macros):
	"""Outlook's Word engine ignores @media and Gmail's handling of a body-level
	<style> is the least predictable part of this design, so every element must
	already be correct from its inline styles alone."""
	shell = _read(EMAIL_DIR, "_shell.html")
	stripped = re.sub(r"<style>.*?</style>", "", shell, flags=re.DOTALL)
	assert stripped.count("padding:") >= 4, "padding must be inline, not only in @media"
	assert "font-size:" in stripped


# ---------------------------------------------------------------------------
# Nothing sends unwrapped.


@pytest.mark.parametrize("rel,src", SENDERS, ids=_ids(SENDERS))
def test_every_sender_uses_the_design_system(rel, src):
	if rel in UNMIGRATED_SENDERS:
		pytest.skip(f"{rel} is on the documented migration backlog")
	assert "email_style" in src, (
		f"{rel} calls frappe.sendmail without email_style. Wrap the body — see "
		"docs/email-design-system.md — or add it to UNMIGRATED_SENDERS with a reason."
	)


def test_migration_backlog_is_empty():
	"""Fails the day someone parks a sender on the backlog and forgets it."""
	assert not UNMIGRATED_SENDERS, f"still unmigrated: {sorted(UNMIGRATED_SENDERS)}"


# ---------------------------------------------------------------------------
# The asset.


def test_logo_ships_in_the_repo():
	"""Not a site File record: those are deletable from the Desk, absent on a
	fresh site, and were how the old emails sourced their logo."""
	path = os.path.join(REPO_ROOT, APP, "public", "images", "email", "logo.png")
	assert os.path.isfile(path), "the email letterhead logo is missing"
	with open(path, "rb") as handle:
		header = handle.read(26)
	assert header[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
	assert header[25] == 2, (
		"the logo must be opaque RGB, not RGBA. Gmail and Outlook.com force-invert "
		"the page in dark mode but never invert image pixels, so a transparent "
		"navy wordmark goes navy-on-dark and disappears."
	)


# ---------------------------------------------------------------------------
# The Notification fixtures.


def _notification_fixtures():
	import json

	with open(os.path.join(REPO_ROOT, APP, "fixtures", "notification.json"), encoding="utf-8") as handle:
		return json.load(handle)


def test_notification_fixtures_are_explicitly_enabled():
	"""Without an explicit ``enabled: 1`` a Notification fixture imports disabled
	and is re-disabled on every migrate, so the alert simply never fires."""
	for rec in _notification_fixtures():
		assert rec.get("enabled") == 1, f"{rec['name']} has no explicit enabled: 1"


def test_notification_bodies_use_the_design_system():
	"""Bodies carry content; the chrome comes from the ee_* globals.

	Before v1.331.0 thirteen of these were 2–5.7k characters of hand-copied
	letterhead in a Desk field, and the other six were plain text with ``\\n``
	line breaks that collapsed to one run-on paragraph in every mail client.
	"""
	for rec in _notification_fixtures():
		message = rec.get("message") or ""
		assert "ee_email(" in message, f"{rec['name']} does not render through ee_email()"
		assert "max-width" not in message, f"{rec['name']} re-imposes a container"
		assert "<img" not in message, f"{rec['name']} inlines a logo; the shell owns the letterhead"
		for hex_value, why in BANNED_HEX.items():
			assert hex_value not in message.lower(), f"{rec['name']} uses {hex_value}: {why}"


def test_notification_fixtures_keep_their_recipients():
	"""The fixture is now what sets these on every migrate, so an empty
	recipients list would silently stop an alert reaching anyone."""
	for rec in _notification_fixtures():
		assert rec.get("recipients"), f"{rec['name']} has no recipients"


def test_every_fixtured_notification_is_listed_in_hooks():
	"""A record in the JSON that the hooks filter does not name is exported by
	nothing and re-imported by nothing — it looks managed and is not."""
	hooks = _read(REPO_ROOT, APP, "hooks.py")
	for rec in _notification_fixtures():
		assert f'"{rec["name"]}"' in hooks, (
			f"{rec['name']} is in notification.json but not in the hooks.py fixture filter"
		)


def test_hooks_registers_the_jinja_globals():
	"""Individually and prefixed — a module-valued hook entry exports every
	function in the module into the global Jinja namespace of every Print Format
	and web template on the site."""
	hooks = _read(REPO_ROOT, APP, "hooks.py")
	for name in ("ee_email", "ee_kv", "ee_button", "ee_doc_button", "ee_email_logo_url"):
		assert f"email_style.{name}" in hooks, f"{name} is not registered in hooks.py"
	assert "email_style.wrap" not in hooks, "wrap must not be a global — too generic a name"
