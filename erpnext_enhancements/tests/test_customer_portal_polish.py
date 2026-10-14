"""Custom pages S6a (TASK-2026-02808): the customer portal's look, presentation only.

/pay, /pay-card, /rentals and /rent-a-fountain are customer-facing, and the first two take
payments. They now share a nav bar (``templates/includes/sf_portal_nav.html``), read the app design
tokens, and have a phone layout (``public/css/sf_portal.bundle.css``). This fails the build if:

* a page stops linking ``sf_tokens.bundle.css`` through ``bundled_asset`` before its own
  ``sf_portal.bundle.css``, loses the light pin before them, stops using the shared nav bar, or if
  ``web_include_css`` starts carrying the tokens (every public page would get them);
* the nav bar grows a Desk or ``/app/`` link, or stops gating the signed-in links and the rent form;
* the portal CSS holds a literal or named colour or a Frappe palette variable, reads a ``--sf-*``
  token that does not exist, declares a custom property, casts a shadow, rounds with anything but an
  ``--sf-radius-*`` token, breaks at a width other than 767px, paints text in a status fill, or sets
  ``display`` on an element /pay-card's script shows and hides;
* a control is under 44px or a field's type under 16px (iOS zooms in);
* **any page script changes by one byte.** The inline scripts are pinned by SHA-256 to what
  origin/main held when this landed (v1.678.0). They build, submit and confirm payments; a change to
  one belongs in a change of its own, with this pin moved in the same commit and the reason given;
* a selector a page script reads (an id, a class, a ``data-`` attribute) is gone from its markup,
  or a form field's ``name`` changed;
* a field has no label, or a template carries an inline style that is not state.

Bench-free: reads files only, no frappe stub. jinja2 renders the nav bar where it is installed.
Run: python -m unittest erpnext_enhancements.tests.test_customer_portal_polish
"""

import ast
import hashlib
import re
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
WWW = APP / "www"
INCLUDES = APP / "templates" / "includes"
NAV = INCLUDES / "sf_portal_nav.html"
LIGHT = INCLUDES / "sf_portal_light.html"
CSS = APP / "public" / "css" / "sf_portal.bundle.css"
TOKENS = APP / "public" / "css" / "sf_tokens.bundle.css"
HOOKS = APP / "hooks.py"
RENTAL_REQUESTS = APP / "asset_management" / "rental_requests.py"

TOKENS_LINK = '<link rel="stylesheet" href="{{ bundled_asset(\'sf_tokens.bundle.css\') }}">'
PORTAL_LINK = '<link rel="stylesheet" href="{{ bundled_asset(\'sf_portal.bundle.css\') }}">'
LIGHT_INCLUDE = '{% include "erpnext_enhancements/templates/includes/sf_portal_light.html" %}'
NAV_BLOCK = '{% block navbar %}{% include "erpnext_enhancements/templates/includes/sf_portal_nav.html" %}{% endblock %}'

# Each page's inline <script> bodies, in order, by SHA-256: origin/main's bytes at v1.669.0, which
# this change left alone. Move a pin only together with a reviewed change to that script.
SCRIPT_PINS = {
	"pay.html": ["2594b21f1effa378663ba86b164bdc833e34f0f5f82f2df72742cbe2d0c6b168"],
	"pay-card.html": ["d1f25ddd7560a8866adac04d80f98b07a811627218ef0eaa1eea33732b2f2856"],
	"rentals.html": ["d64ca33cb658d6a3a8416ce6d80bbee9b733b916c21aad824108a6efb5094ce8"],
	"rent-a-fountain.html": ["29c6ea053de18be5025c99c74ed101594aa5d0a59d335c1a5a7e028cf6717883"],
}

# What each page's script reads from the markup. The extraction below must find at least these
# (so it cannot pass by finding nothing), and everything it finds must exist outside the script.
EXPECTED_SELECTORS = {
	"pay.html": {
		"class:pay-btn",
		"data:invoice",
		"data:method",
		"id:stripe-autopay-consent",
		"id:stripe-setup-autopay",
		"id:stripe-cancel-autopay",
	},
	"pay-card.html": {
		"id:payment-element",
		"id:step-card",
		"id:step-review",
		"id:continue-btn",
		"id:card-error",
		"id:review-amount",
		"id:review-fee-row",
		"id:review-fee-label",
		"id:review-fee",
		"id:review-total",
		"id:review-disclosure",
		"id:review-nofee",
		"id:review-error",
		"id:pay-btn",
		"id:back-btn",
	},
	"rentals.html": {"id:rental-site-prep", "id:rental-change", "class:rental-status", "data:booking"},
	"rent-a-fountain.html": {"id:rr-form", "id:rr-status", "id:rr-done", "id:rr-turnstile"},
}

# Every form field's name, as the endpoints behind these pages read them.
FIELD_NAMES = {
	"pay.html": [],
	"pay-card.html": [],
	"rentals.html": [
		"site_contact_name",
		"site_contact_phone",
		"surface",
		"site_access",
		"power_source",
		"water_source",
		"site_prep_notes",
		"message",
	],
	"rent-a-fountain.html": [
		"first_name",
		"last_name",
		"email",
		"phone",
		"organization",
		"event_name",
		"event_date",
		"end_date",
		"venue",
		"zip",
		"guests",
		"interest",
		"notes",
		"{{ boot.honeypot }}",
	],
}

# Inline style attributes a page may keep, each because it is state or function, not look.
ALLOWED_INLINE_STYLES = {
	# /pay-card's script shows and hides these through style.display and reads it back.
	"pay-card.html": [
		('id="step-review"', "display:none;"),
		('id="review-fee-row"', "display:none;"),
		('id="review-disclosure"', "display:none;"),
		('id="review-nofee"', "display:none;"),
	],
	# The honeypot must stay off screen even if the stylesheet never arrives.
	"rent-a-fountain.html": [("aria-hidden", "position:absolute;left:-10000px")],
}

# Elements /pay-card's script toggles with style.display = "none" / "": "" must fall back to the
# element's natural display, so no stylesheet rule may set one for them.
TOGGLED_IDS = ("step-card", "step-review", "review-fee-row", "review-disclosure", "review-nofee")

FRAPPE_PALETTE = re.compile(
	r"var\(\s*--(?:"
	r"(?:red|green|yellow|orange|gray|grey|blue|purple|pink|cyan)-\d"
	r"|text-on-[\w-]+"
	r"|bg-(?!color\b)[\w-]+"
	r"|border-color\b|text-color\b|primary\b"
	r")"
)

NAMED_COLOURS = re.compile(
	r"(?<![\w-])(?:white|black|red|green|blue|yellow|orange|gray|grey|purple|pink|silver|navy"
	r"|teal|maroon|olive|lime|aqua|fuchsia|cyan|magenta|brown|gold|indigo|violet|crimson"
	r"|tomato|salmon|coral|khaki|beige|ivory|lavender|tan|turquoise|whitesmoke|gainsboro)"
	r"(?![\w-])",
	re.I,
)

STATUS_FILLS = ("--sf-warn", "--sf-good", "--sf-danger")


def _read(path):
	return path.read_text(encoding="utf-8")


def _css_code(text):
	return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _rules(css):
	"""(selector prelude, body) for every innermost rule, @media contents included."""
	return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)]


def _declarations(body):
	out = []
	for decl in body.split(";"):
		if ":" in decl:
			prop, value = decl.split(":", 1)
			out.append((prop.strip().lower(), value.strip()))
	return out


def _all_declarations(css):
	return [d for _sel, body in _rules(css) for d in _declarations(body)]


def _block(template, name):
	start = template.index("{% block " + name + " %}")
	return template[start : template.index("{% endblock %}", start) + len("{% endblock %}")]


def _scripts(template):
	return re.findall(r"<script>(.*?)</script>", template, flags=re.S)


def _markup(template):
	"""The template without its inline scripts and Jinja comments."""
	text = re.sub(r"<script>.*?</script>", "", template, flags=re.S)
	return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


def _js_code(text):
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def _selectors_read(script):
	"""Every id, class and data- attribute a page script reads from the DOM."""
	code = _js_code(script)
	found = set()
	for m in re.finditer(r"(?:getElementById|\$)\(\s*\"([\w-]+)\"\s*\)", code):
		found.add("id:" + m.group(1))
	for m in re.finditer(r"\bwire\(\s*\"([\w-]+)\"", code):
		found.add("id:" + m.group(1))
	for m in re.finditer(r"\(\s*\"#([\w-]+)\"", code):
		found.add("id:" + m.group(1))
	for m in re.finditer(r"querySelector(?:All)?\(\s*\"([^\"]+)\"\s*\)", code):
		for kind, name in re.findall(r"([.#])([\w-]+)", m.group(1)):
			found.add(("class:" if kind == "." else "id:") + name)
	for m in re.finditer(r"getAttribute\(\s*\"data-([\w-]+)\"\s*\)", code):
		found.add("data:" + m.group(1))
	for m in re.finditer(r"\.dataset\.(\w+)", code):
		found.add("data:" + re.sub(r"[A-Z]", lambda c: "-" + c.group(0).lower(), m.group(1)))
	return found


def _present(markup, selector):
	kind, name = selector.split(":", 1)
	if kind == "id":
		return f'id="{name}"' in markup
	if kind == "class":
		return re.search(r'class="[^"]*(?<![\w-])' + re.escape(name) + r"(?![\w-])", markup) is not None
	return f"data-{name}=" in markup


class TestEachPageLinksTheTokensAndTheNav(unittest.TestCase):
	def test_the_head_links_the_tokens_before_the_portal_stylesheet(self):
		for page in SCRIPT_PINS:
			head = _block(_read(WWW / page), "head_include")
			with self.subTest(page=page):
				self.assertEqual(
					head.count(TOKENS_LINK), 1, "link sf_tokens.bundle.css through bundled_asset"
				)
				self.assertEqual(
					head.count(PORTAL_LINK), 1, "link sf_portal.bundle.css through bundled_asset"
				)
				self.assertLess(head.index(TOKENS_LINK), head.index(PORTAL_LINK))
				# The light pin comes first, so the page never paints dark.
				self.assertLess(head.index(LIGHT_INCLUDE), head.index(TOKENS_LINK))

	def test_the_pages_that_kept_the_site_head_still_do(self):
		"""/pay, /pay-card and /rentals never overrode head_include, so they had the site's own head
		HTML; super() keeps it. /rent-a-fountain overrode it before this change and still does."""
		for page in ("pay.html", "pay-card.html", "rentals.html"):
			with self.subTest(page=page):
				self.assertIn("{{ super() }}", _block(_read(WWW / page), "head_include"))
		head = _block(_read(WWW / "rent-a-fountain.html"), "head_include")
		self.assertIn('<meta name="robots" content="noindex, nofollow">', head)

	def test_every_page_uses_the_shared_nav_bar(self):
		for page in SCRIPT_PINS:
			with self.subTest(page=page):
				self.assertIn(NAV_BLOCK, _read(WWW / page))

	def test_every_page_wraps_its_content_in_the_portal_class(self):
		for page in SCRIPT_PINS:
			markup = _markup(_read(WWW / page))
			with self.subTest(page=page):
				# tabindex="-1": following the skip link moves keyboard focus here, not just the view.
				self.assertRegex(
					markup, r'<div class="[^"]*\bsf-portal\b[^"]*" id="sf-portal-main" tabindex="-1">'
				)

	def test_no_raw_asset_path_for_the_new_css(self):
		for page in SCRIPT_PINS:
			text = _read(WWW / page)
			with self.subTest(page=page):
				self.assertNotIn("/assets/erpnext_enhancements/css/sf_", text)

	def test_the_tokens_are_not_a_website_wide_include(self):
		hooks = _read(HOOKS)
		for hook in ("web_include_css", "web_include_js"):
			m = re.search(rf"^{hook}\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", hooks, flags=re.M)
			self.assertIsNotNone(m, hook)
			with self.subTest(hook=hook):
				self.assertNotIn("sf_tokens", m.group(1))
				self.assertNotIn("sf_portal", m.group(1))

	def test_the_light_pin_writes_the_tokens_own_switch(self):
		code = re.sub(r"\{#.*?#\}", "", _read(LIGHT), flags=re.S)
		self.assertIn('document.documentElement.setAttribute("data-theme", "light");', code)
		self.assertEqual(len(_scripts(code)), 1)


class TestTheNavBar(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.raw = _read(NAV)
		cls.code = re.sub(r"\{#.*?#\}", "", cls.raw, flags=re.S)

	def test_no_desk_links(self):
		hrefs = re.findall(r'href="([^"]*)"', self.code)
		self.assertGreaterEqual(len(hrefs), 6)
		for href in hrefs:
			with self.subTest(href=href):
				self.assertFalse(href.startswith(("/app", "/desk", "/apps")), href)

	def test_customer_links(self):
		for href in ("/pay", "/rentals", "/rent-a-fountain", "/logout", "/login?redirect-to="):
			with self.subTest(href=href):
				self.assertIn(f'href="{href}', self.code)

	def test_it_is_a_labelled_nav_with_a_skip_link(self):
		self.assertRegex(self.code, r"<nav class=\"sf-portal-nav\" aria-label=")
		self.assertIn('href="#sf-portal-main"', self.code)

	def test_the_rent_form_global_is_registered_and_exists(self):
		hooks = _read(HOOKS)
		self.assertIn('"erpnext_enhancements.asset_management.rental_requests.ee_rent_form_enabled"', hooks)
		tree = ast.parse(_read(RENTAL_REQUESTS))
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "ee_rent_form_enabled")
		self.assertEqual(ast.unparse(fn.body[-1]), "return enabled()")
		self.assertFalse(fn.decorator_list, "a Jinja global, not an endpoint")

	def _render(self, user, path, rent_form):
		try:
			import jinja2
		except ImportError:
			self.skipTest("jinja2 is not installed")
		env = jinja2.Environment()
		env.globals["_"] = lambda text, *a, **k: text
		env.globals["ee_rent_form_enabled"] = lambda: rent_form
		frappe = types.SimpleNamespace(session=types.SimpleNamespace(user=user))
		return env.from_string(self.raw).render(frappe=frappe, path=path)

	def _links(self, html):
		return {
			text.strip(): (href, "aria-current" in attrs)
			for href, attrs, text in re.findall(r'<a href="([^"]*)"([^>]*)>([^<]*)</a>', html)
		}

	def test_signed_in_on_pay_card(self):
		links = self._links(self._render("customer@example.com", "pay-card", True))
		self.assertEqual(
			links,
			{
				"Pay Invoices": ("/pay", True),
				"My Rentals": ("/rentals", False),
				"Rent a Fountain": ("/rent-a-fountain", False),
				"Sign out": ("/logout", False),
			},
		)

	def test_the_rent_form_link_waits_for_the_form(self):
		links = self._links(self._render("customer@example.com", "rentals", False))
		self.assertNotIn("Rent a Fountain", links)
		self.assertEqual(links["My Rentals"], ("/rentals", True))

	def test_a_guest_on_the_rent_form(self):
		# The form is open (the page renders only then), so the link shows without asking.
		links = self._links(self._render("Guest", "rent-a-fountain", False))
		self.assertEqual(
			links,
			{
				"Rent a Fountain": ("/rent-a-fountain", True),
				"Sign in": ("/login?redirect-to=/rent-a-fountain", False),
			},
		)


class TestThePortalCss(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.raw = _read(CSS)
		cls.css = _css_code(cls.raw)
		cls.tokens = set(re.findall(r"(--sf-[a-z0-9-]+)\s*:", _css_code(_read(TOKENS))))

	def test_no_literal_or_palette_colours(self):
		for sel, body in _rules(self.css):
			with self.subTest(rule=sel[:60]):
				self.assertNotRegex(body, r"#[0-9a-fA-F]{3,8}\b")
				self.assertNotRegex(body, r"(?i)\b(?:rgba?|hsla?)\(")
				self.assertNotRegex(body, FRAPPE_PALETTE)
		for prop, value in _all_declarations(self.css):
			if prop == "content":
				continue
			with self.subTest(declaration=f"{prop}: {value}"):
				self.assertNotRegex(value, NAMED_COLOURS)

	def test_every_token_read_is_defined(self):
		used = set(re.findall(r"var\(\s*(--sf-[a-z0-9-]+)", self.css))
		self.assertGreater(len(used), 30)
		self.assertEqual(sorted(used - self.tokens), [], "not defined in sf_tokens.bundle.css")

	def test_it_declares_no_custom_property(self):
		self.assertEqual([p for p, _v in _all_declarations(self.css) if p.startswith("--")], [])

	def test_no_shadow_except_switching_bootstraps_off(self):
		for prop, value in _all_declarations(self.css):
			if "shadow" in prop or "shadow" in value:
				with self.subTest(declaration=f"{prop}: {value}"):
					self.assertEqual((prop, value), ("box-shadow", "none"))

	def test_every_radius_is_a_token(self):
		for prop, value in _all_declarations(self.css):
			if prop.startswith("border") and "radius" in prop:
				with self.subTest(value=value):
					self.assertRegex(value, r"^var\(--sf-radius-[a-z]+\)$")

	def test_square_cards_pill_chips_rounded_buttons(self):
		radius = {
			sel: value
			for sel, body in _rules(self.css)
			for prop, value in _declarations(body)
			if prop == "border-radius"
		}
		self.assertEqual(radius[".sf-portal .card"], "var(--sf-radius-none)")
		self.assertEqual(radius[".sf-portal .alert"], "var(--sf-radius-none)")
		self.assertEqual(radius[".sf-portal .form-control"], "var(--sf-radius-none)")
		self.assertEqual(radius[".sf-portal .btn"], "var(--sf-radius-control)")
		self.assertEqual(radius[".sf-portal-chip"], "var(--sf-radius-pill)")

	def test_one_phone_breakpoint(self):
		widths = re.findall(r"@media[^{]*\((?:max|min)-width\s*:\s*([^)]+)\)", self.css)
		self.assertEqual(widths, ["767px"])

	def test_touch_targets_and_input_type(self):
		decls = {sel: dict(_declarations(body)) for sel, body in _rules(self.css)}
		for sel in (
			".sf-portal .btn",
			".sf-portal .form-control",
			".sf-portal-nav__links a",
			".sf-portal-skip",
		):
			with self.subTest(selector=sel):
				self.assertEqual(decls[sel].get("min-height"), "var(--sf-touch-min)")
		self.assertEqual(decls[".sf-portal .form-control"]["font-size"], "var(--sf-text-input)")

	def test_no_status_fill_is_text(self):
		for prop, value in _all_declarations(self.css):
			if prop != "color":
				continue
			compact = value.replace(" ", "").replace("!important", "")
			with self.subTest(declaration=f"{prop}: {value}"):
				for fill in STATUS_FILLS:
					self.assertNotEqual(compact, f"var({fill})", "a status fill used as text")
					if f"var({fill})" in compact:
						self.assertIn(f"color-mix(insrgb,var({fill})50%,var(--sf-text-strong))", compact)

	def test_chips_carry_their_label_colour_and_edge(self):
		decls = {sel: dict(_declarations(body)) for sel, body in _rules(self.css)}
		for tone, fill, label in (
			("neutral", "--sf-pillar-neutral-deep", "--sf-on-pillar"),
			("warn", "--sf-warn", "--sf-on-warn"),
			("rent", "--sf-pillar-rent-deep", "--sf-on-pillar"),
		):
			chip = decls[f".sf-portal-chip--{tone}"]
			with self.subTest(tone=tone):
				self.assertEqual(chip["background-color"], f"var({fill})")
				self.assertEqual(chip["color"], f"var({label})")
				self.assertEqual(chip["border-color"], f"var(--sf-chip-edge-{tone})")

	def test_the_dark_mode_traps(self):
		self.assertNotRegex(self.css, r"var\(\s*--sf-pillar-neutral\s*\)")
		self.assertNotRegex(self.css, r"var\(\s*--sf-pillar-build-deep\s*\)")

	def test_no_rule_sets_display_on_what_the_script_toggles(self):
		for sel, body in _rules(self.css):
			if not any(prop == "display" for prop, _v in _declarations(body)):
				continue
			for toggled in TOGGLED_IDS:
				with self.subTest(rule=sel, id=toggled):
					self.assertNotRegex(sel, rf"#{toggled}(?![\w-])")

	def test_focus_is_styled_not_removed(self):
		self.assertNotRegex(self.css, r"outline\s*:\s*(?:none|0)\b")
		self.assertIn(":focus-visible", self.css)


class TestThePageScriptsDidNotMove(unittest.TestCase):
	def test_each_inline_script_is_byte_identical(self):
		for page, pins in SCRIPT_PINS.items():
			bodies = _scripts(_read(WWW / page))
			with self.subTest(page=page):
				self.assertEqual(
					[hashlib.sha256(b.encode("utf-8")).hexdigest() for b in bodies],
					pins,
					"a page script changed; presentation work must not touch it",
				)

	def test_stripe_js_is_still_stripes_own(self):
		text = _read(WWW / "pay-card.html")
		self.assertEqual(text.count('<script src="https://js.stripe.com/v3/"></script>'), 1)
		self.assertEqual(re.findall(r"<script src=\"([^\"]+)\"", text), ["https://js.stripe.com/v3/"])

	def test_every_selector_a_script_reads_is_still_in_the_markup(self):
		for page, expected in EXPECTED_SELECTORS.items():
			template = _read(WWW / page)
			found = set().union(*(_selectors_read(s) for s in _scripts(template)))
			markup = _markup(template)
			with self.subTest(page=page):
				self.assertLessEqual(expected, found, "the extraction missed a selector it should see")
				self.assertEqual(sorted(s for s in found if not _present(markup, s)), [])

	def test_the_form_field_names_are_unchanged(self):
		for page, names in FIELD_NAMES.items():
			markup = _markup(_read(WWW / page))
			with self.subTest(page=page):
				self.assertEqual(
					re.findall(r'<(?:input|select|textarea)\b[^>]*?\bname="([^"]+)"', markup), names
				)


class TestAccessibilityAndInlineStyles(unittest.TestCase):
	def test_the_invoice_table_keeps_its_roles_when_it_becomes_cards(self):
		"""On a phone the CSS sets display:block on the table's parts, and Safari/VoiceOver then
		drops the table semantics unless the roles are explicit."""
		markup = _markup(_read(WWW / "pay.html"))
		table = markup[markup.index("<table") : markup.index("</table>")]
		self.assertRegex(table, r'<table class="[^"]*\bsf-portal-invoices\b[^"]*" role="table">')
		self.assertEqual(len(re.findall(r"<(?:thead|tbody)\b", table)), 2)
		self.assertEqual(len(re.findall(r'<(?:thead|tbody) role="rowgroup">', table)), 2)
		for tag in ("tr", "th", "td"):
			role = {"tr": "row", "th": "columnheader", "td": "cell"}[tag]
			tags = re.findall(rf"<{tag}\b[^>]*>", table)
			with self.subTest(tag=tag):
				self.assertGreater(len(tags), 1)
				for found in tags:
					self.assertIn(f'role="{role}"', found)

	def test_every_field_has_a_label(self):
		for page in SCRIPT_PINS:
			markup = _markup(_read(WWW / page))
			fors = set(re.findall(r'<label for="([^"]+)"', markup))
			for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", markup):
				if 'type="hidden"' in tag or 'tabindex="-1"' in tag:
					continue  # the honeypot: aria-hidden, inside its own label, never for people
				with self.subTest(page=page, field=tag[:80]):
					fid = re.search(r'\bid="([^"]+)"', tag)
					labelled = "aria-label=" in tag or "aria-labelledby=" in tag
					wrapped = 'type="checkbox"' in tag  # /pay's consent box sits inside its label
					self.assertTrue(labelled or wrapped or (fid and fid.group(1) in fors), tag)

	def test_no_inline_style_block(self):
		for page in SCRIPT_PINS:
			with self.subTest(page=page):
				self.assertNotIn("<style", _read(WWW / page))

	def test_inline_style_attributes_are_state_only(self):
		for page in SCRIPT_PINS:
			markup = _markup(_read(WWW / page))
			found = []
			for tag in re.findall(r"<[a-z]+\b[^>]*\bstyle=\"[^\"]*\"[^>]*>", markup):
				found.append(re.search(r'style="([^"]*)"', tag).group(1))
				allowed = ALLOWED_INLINE_STYLES.get(page, [])
				with self.subTest(page=page, tag=tag[:80]):
					self.assertTrue(any(marker in tag and style in tag for marker, style in allowed), tag)
			with self.subTest(page=page):
				self.assertEqual(len(found), len(ALLOWED_INLINE_STYLES.get(page, [])))


if __name__ == "__main__":
	unittest.main()
