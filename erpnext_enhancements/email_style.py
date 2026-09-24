# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The email design system — the one place an email's chrome and components live.

This module is the email twin of ``project_enhancements/contract_style.py``, and
it is deliberately shaped like it: a :func:`wrap` that adds chrome around a body
it does not touch, a cached logo accessor, and degradation to plain output rather
than a raised exception, because an email that loses its letterhead is a nuisance
and an email that raises inside a background job is a lost notification.

It diverges from ``contract_style`` in exactly one place, and for one reason.
That module inlines the logo as ``<svg>`` because its renderer is wkhtmltopdf.
**Gmail and Outlook strip inline SVG entirely**, so email uses a hosted ``<img>``.

**Three caller shapes, one layout.** The app emails from three surfaces and they
cannot be unified into one calling convention, so the layout is unified instead:

===========================  =========================  ========================
Surface                      Shape                      Entry point
===========================  =========================  ========================
``templates/emails/*.html``  Jinja file template        ``{% extends %}`` _shell
~26 ``sendmail`` callers     Python holding an HTML     :func:`wrap` / :func:`render`
                             string
19 ``Notification`` rows      Jinja string in the DB     the ``ee_*`` globals
===========================  =========================  ========================

The markup itself exists **once**, in ``templates/emails/_components.html``, as
pure Jinja macros. The Python functions below are thin adapters over those
macros, reached through Jinja's own module API rather than reimplemented — so
there is no second copy of a ``<td>`` to drift.

Notification bodies use the ``ee_*`` globals rather than ``{% extends %}``, and
that is a considered choice: ``{% extends %}`` does work from a DB-stored string
(frappe's jenv keeps its loader through ``overlay()``), but in a child template
anything outside a ``{% block %}`` is *silently discarded*, and a typo in the
extends path raises at send time inside ``Notification.send()``'s own
``except``, which logs an Error Log and drops the email. Neither failure is
survivable in a field people edit through the Desk.

The palette and the pillar records are the Sapphire Fountains design system's
tokens; ``print_style`` holds the pillar records so paper and email agree, and
this module imports them rather than copying them.

See ``docs/email-design-system.md`` for the guide and the full inventory.
"""

import functools

import frappe

from erpnext_enhancements.print_style import PILLARS as _PILLARS
from erpnext_enhancements.print_style import pillar as _pillar
from erpnext_enhancements.print_style import stripe_css as _stripe_css
from erpnext_enhancements.utils.deploy import get_deploy_version
from erpnext_enhancements.utils.url_safety import is_safe_url

SHELL = "erpnext_enhancements/templates/emails/_shell.html"
COMPONENTS = "erpnext_enhancements/templates/emails/_components.html"

LOGO_ASSET = "/assets/erpnext_enhancements/images/email/logo.png"

# The measure. Fluid to the width of the reading pane, capped here so body copy
# does not run 1400px wide on a maximised desktop window. Set to None for
# full-bleed; the shell drops both the cap and its Outlook ghost table.
MAX_WIDTH = 840

# ---------------------------------------------------------------------------
# The palette. These are the only email colours in the app: the guard test
# asserts every hex literal in _shell.html and _components.html is one of them.
#
# Since v1.494.0 every value is a token of the Sapphire Fountains design system
# (named beside it), applied as the "Pillar Stripe" concept Nik picked from the
# design canvas on 2026-09-21. The two status colours the token set lacks come
# from the 2016 brand guide's supportive set, as the design system directs.
#
# Contrast against white is measured, not assumed, because the two values the
# first palette replaced both failed AA and the app already gated on that rule
# elsewhere (scripts/test_chat_source_rules.js banned #00a0dd as text at 2.97:1).
# Rejected then and still banned: #1E9E5A, the old CTA fill, 3.52:1 with white
# text; #00A1DE, the old link colour, 2.90:1.
INK = "#00263e"  # deep-sea-blue — titles, table rules — 14.1:1
BODY_INK = "#363636"  # ink-700 — body copy — 12.1:1
MUTED = "#363636"  # the design system has no muted grey: small print is ink-700, smaller
ACCENT = "#00609c"  # bahama-blue — links, labels, sub-heads on the light ground — 6.7:1
SUCCESS = "#316564"  # teal-deep — completed, submitted, paid — 6.4:1
# The brand has no warm text colour. The guide's Gold (#8e7631) measures 4.39:1
# on white — under AA — so a warning is ink-700 text under a yellow rule, and the
# words carry the severity, as they must anyway under Gmail's forced dark mode.
WARNING = "#363636"
DANGER = "#bd2e2b"  # red (brand guide supportive set) — failed, error — 5.9:1
RULE = "#dadbdd"  # border-100 — hairlines. Never carries text.
SURFACE = "#f8f8f8"  # off-white — the ground of a callout or KPI. Never carries text.

# Fills and rules. fresh-blue is the design system's "signature sapphire" and the
# fill of every primary button; its label is navy-900 (6.5:1) rather than the
# website's off-white (2.8:1), which the design system itself flags as its most
# visible contrast failure. teal and yellow are rule colours only — 1.8:1 and
# 1.7:1 as text — which is why they appear as `br` and never as `fg`.
FRESH_BLUE = "#00a0df"
NAVY_900 = "#00111c"
TEAL = "#62cbc9"
YELLOW = "#ffb819"

# Tone fills and tints, mirrored in _components.html. The guard test asserts
# every hex literal in the two template files appears somewhere in this module,
# which is what keeps the two copies from drifting. `fg` is the text, `br` the
# rule along the top of a callout or KPI and the edge of a pill, `fill`/`label`
# the button.
TONE_COLORS = {
	"primary": {"fg": ACCENT, "bg": SURFACE, "br": FRESH_BLUE, "fill": FRESH_BLUE, "label": NAVY_900},
	"success": {"fg": SUCCESS, "bg": SURFACE, "br": TEAL, "fill": SUCCESS, "label": SURFACE},
	"warning": {"fg": WARNING, "bg": SURFACE, "br": YELLOW, "fill": WARNING, "label": SURFACE},
	"danger": {"fg": DANGER, "bg": SURFACE, "br": DANGER, "fill": DANGER, "label": SURFACE},
	"info": {"fg": ACCENT, "bg": SURFACE, "br": FRESH_BLUE, "fill": FRESH_BLUE, "label": NAVY_900},
}

TONES = tuple(TONE_COLORS)

# Used by the shell only. White is the page: the design deliberately has no
# tinted canvas and no card, because a card is what made the old emails read as
# a narrow centred box.
WHITE = "#ffffff"

# ---------------------------------------------------------------------------
# The pillars. The four things the company sells each own a colour, and a mail
# that belongs to one carries its stripe and its name; everything else — alerts,
# digests, anything internal — takes the brand's dark band and no name. The
# records themselves live in print_style so paper and email cannot disagree.
PILLARS = tuple(_PILLARS)


def pillar_context(key):
	"""The shell's `pillar` variable for ``key``: name, open, deep and the
	stripe's CSS. ``None`` or an unknown key is the neutral dark band."""
	record = _pillar(key)
	return {
		"name": record["name"],
		"open": record["open"],
		"deep": record["deep"],
		"stripe": _stripe_css(key),
	}


@functools.lru_cache(maxsize=1)
def logo_url():
	"""Absolute, cache-busted URL of the email letterhead logo.

	Absolute because an email client has no site origin to resolve against.
	Cache-busted with the deploy token because ``/assets`` is served immutable
	for a year with no content hash, so an edited logo would never reach a
	client that had already cached one.

	Note this is computed in Python rather than written into the template: the
	``deploy_version`` used elsewhere for this is *page context* set by each
	``www/`` controller, not a Jinja global, so a template cannot reach it.
	"""
	try:
		return f"{frappe.utils.get_url(LOGO_ASSET)}?v={get_deploy_version()}"
	except Exception:
		# A logo is not worth failing a send over; the shell falls back to a
		# styled wordmark in text.
		return ""


def _log(message):
	"""Log, and never raise doing it.

	``frappe.log_error`` is not safe to call from inside an ``except`` that is
	guarding a send: it writes to the database, so on a dead connection it raises
	and the "never raises" promise below quietly becomes false — the same shape as
	the background-job trap in CLAUDE.md. CI found it the blunter way, through a
	test stub whose ``log_error`` took a different signature.
	"""
	try:
		frappe.log_error(message)
	except Exception:
		pass


def _components():
	"""The component macros as a Jinja module.

	Deliberately not cached: ``frappe.get_template`` already hits the jenv
	template cache, and caching the module object here would only defeat the
	dev-server template reload.
	"""
	return frappe.get_template(COMPONENTS).make_module({})


# ---------------------------------------------------------------------------
# The two entry points.


def wrap(
	body_html, *, title=None, eyebrow=None, preheader=None, footer_note=None, tagline=False, pillar=None
):
	"""``body_html`` inside the standard letterhead, heading and footer.

	The body is passed through untouched — this adds chrome, it does not restyle
	what it is given. That matters for the two bodies the app does not own:
	``md_to_html`` output in the morning briefing, and ``Auto Email Report``
	content in the commission report.

	``tagline`` adds "Add Water. Make Magic." and is for customer-facing mail;
	it reads oddly under an Error Log alert, so it is off by default.

	``pillar`` is ``"service"``, ``"build"``, ``"design"`` or ``"rent"`` for a
	mail that belongs to one of the four things the company sells — it colours
	the stripe and names the pillar in the letterhead — and ``None`` for
	everything else, which takes the brand's dark band. Customer-facing mail
	almost always has one; an Error Log alert does not.

	Never raises. A failure here would take an otherwise-deliverable email down
	with it, so the unwrapped body is returned instead.
	"""
	try:
		return frappe.render_template(
			SHELL,
			{
				"body": body_html or "",
				"title": title,
				"eyebrow": eyebrow,
				"preheader": preheader,
				"footer_note": footer_note,
				"tagline": tagline,
				"pillar": pillar_context(pillar),
				"logo_url": logo_url(),
				"site_url": frappe.utils.get_url(),
			},
		)
	except Exception:
		_log("email_style.wrap failed; sent unwrapped")
		return body_html or ""


def render(template, context=None, **kwargs):
	"""Render a body template, then :func:`wrap` it.

	The convenience form for the callers that already hold a template path. Note
	the two-step: callers that also write a ``Notification Log`` row want the
	*unwrapped* fragment for the bell panel, and can call
	``frappe.render_template`` and :func:`wrap` separately to get both.
	"""
	return wrap(frappe.render_template(template, context or {}), **kwargs)


# ---------------------------------------------------------------------------
# Components. Each delegates to the macro of the same name; none of them
# contains markup.


def _macro(name, *args):
	try:
		return str(getattr(_components(), name)(*args))
	except Exception:
		_log(f"email_style.{name} failed")
		return ""


def h(text):
	return _macro("h", text)


def p(text):
	return _macro("p", text)


def rich(html):
	"""A paragraph of caller-authored markup — prose carrying inline ``<b>`` or
	a link. Its argument is **not** escaped, so escape every value you
	interpolate. Prefer :func:`p`; reach for this only when the sentence needs
	inline markup."""
	return _macro("rich", html)


def note(text):
	"""Small print — the explanatory aside under the main content."""
	return _macro("note", text)


def note_rich(html):
	"""Small print carrying inline markup. Same escaping contract as :func:`rich`."""
	return _macro("note_rich", html)


def prose(text):
	"""Human sentences containing newlines. Body font, newlines kept, no box."""
	return _macro("prose", text)


def code(text):
	"""Machine output — a traceback, a transcript, a backup log."""
	return _macro("code", text)


def kv(rows):
	"""Label/value facts. ``rows`` is ``[(label, value), ...]``."""
	return _macro("kv", rows)


def table(headers, rows):
	"""A data table. Four columns or fewer — five will not fit a phone."""
	return _macro("table", headers, rows)


def kpis(cards):
	"""Headline numbers. ``cards`` is ``[{"label", "value", "tone"}, ...]``."""
	return _macro("kpis", cards)


def callout(text, tone="info"):
	return _macro("callout", text, tone)


def bullets(items):
	return _macro("bullets", items)


def links(items):
	"""``items`` is ``[(url, label), ...]``. Unsafe URLs are dropped."""
	return _macro("links", [(u, label) for u, label in items if is_safe_url(u)])


def button(url, label, tone="primary"):
	"""The call to action.

	An unsafe URL degrades to plain bold text rather than a live ``href`` —
	``utils.url_safety`` is sound rather than exact, which is the right trade
	for something the recipient is being invited to click.
	"""
	if not is_safe_url(url):
		_log(f"email_style.button refused an unsafe URL: {url!r}")
		return p(label)
	return _macro("button", url, label, tone)


def button_fallback(url):
	"""The copy-this-link escape hatch, for mail clients that strip buttons."""
	return _macro("button_fallback", url) if is_safe_url(url) else ""


def pill(label, tone="info"):
	return _macro("pill", label, tone)


def markdown(md_text):
	"""Markdown rendered into the house style.

	The generated tags carry no attributes, so no inline styling can reach them.
	The ``.ee-md`` descendant rules in the shell do it instead — premailer
	inlines them onto the generated elements when the email is sent (verified on
	this site: ``<h2 id="heading" style="color:#00263E; font-size:18px">``).
	"""
	return f'<div class="ee-md">{frappe.utils.md_to_html(md_text or "")}</div>'


def raw(html):
	"""HTML this app did not generate and must not restyle, passed through.

	Two legitimate callers, both bodies produced by frappe core:
	``Auto Email Report.get_report_content()`` and pre-rendered feedback bodies.
	Anything else should use a component; the guard test counts occurrences.
	"""
	return html or ""


# ---------------------------------------------------------------------------
# Jinja globals for Notification bodies, registered individually in hooks.py.
#
# Individually, and prefixed, because get_jinja_hooks exports *every* function
# of a module-valued hook entry into the global Jinja namespace — which would
# put `wrap`, `table` and `render` into the environment of every Print Format
# and web template on the site.


def ee_email(body, title=None, eyebrow=None, preheader=None, tagline=False, pillar=None):
	return wrap(body, title=title, eyebrow=eyebrow, preheader=preheader, tagline=tagline, pillar=pillar)


def ee_button(url, label, tone="primary"):
	return button(url, label, tone)


def ee_doc_button(doc, label, tone="primary"):
	"""A CTA pointing at ``doc``'s form, built here so the body need not know
	how to construct a desk URL."""
	return button(frappe.utils.get_url_to_form(doc.doctype, doc.name), label, tone)


def ee_kv(rows):
	return kv(rows)


def ee_table(headers, rows):
	return table(headers, rows)


def ee_kpis(cards):
	return kpis(cards)


def ee_callout(text, tone="info"):
	return callout(text, tone)


def ee_code(text):
	return code(text)


def ee_prose(text):
	return prose(text)


def ee_pill(label, tone="info"):
	return pill(label, tone)


# `ee_h` and `ee_p` were called by four enabled Notification bodies from v1.331.0 --
# "Maintenance Finalized" and "Maintenance Contract Renewal Due" (ee_p), "High Escalation
# Risk Call" and "Compliance Flag on Call" (ee_h) -- but never defined. Calling an undefined
# Jinja global raises, and Notification.send() catches it, writes an Error Log and sends
# nothing, so each of those emails would have been dropped the first time it fired (none
# had yet, 2026-09-24). test_email_design now fails the build on any `ee_*` a fixture calls
# that hooks.py does not register.


def ee_h(text):
	return h(text)


def ee_p(text):
	return p(text)


def ee_email_logo_url():
	""":func:`logo_url` under a prefixed name.

	get_jinja_hooks registers a function-valued hook entry under its own
	``__name__``, so exposing ``logo_url`` directly would put a global by that
	generic name into every Print Format and web template on the site.
	"""
	return logo_url()
