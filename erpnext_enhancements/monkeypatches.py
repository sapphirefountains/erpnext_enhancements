"""Runtime monkeypatches applied to the Frappe framework on worker startup.

Carried in app code (instead of editing ``apps/frappe`` directly) so they
survive ``bench update``. Triggered from the bottom of ``hooks.py`` — Frappe
imports every installed app's ``hooks`` module in every worker the first time it
loads hooks, so ``apply()`` runs once per process — in web, background, and
scheduler workers alike — before any patched code path is reached. Every patch
is idempotent and self-guarding.
"""

import functools

import frappe
import frappe.utils.modules as _frappe_modules


def _patch_get_modules_from_app_none_safe():
	"""Never let a cached ``None`` from one app crash the whole module list.

	``get_modules_from_all_apps`` does ``modules_list += get_modules_from_app(app)``
	with no guard. ``get_modules_from_app`` is ``@redis_cache``-decorated, and that
	decorator *deliberately* returns ``None`` when a ``None`` was previously cached
	for a key (frappe/utils/caching.py — the "Edge Case: None can mean cache miss
	or the result itself is None" branch). So a single poisoned cache entry — e.g.
	a transient empty/failed ``Module Def`` query for an app such as ``telephony``
	— makes ``list += None`` raise ``TypeError: 'NoneType' object is not iterable``
	for the rest of that entry's TTL, taking down everything that lists modules:
	CRM's ``check_app_permission`` (the app switcher), Dashboard / Dashboard Chart
	/ Number Card, and the User / Module Profile forms.

	Wrapping the function so it can never return ``None`` is equivalent to the
	upstream one-liner ``get_modules_from_app(app) or []``, but protects every
	caller rather than only the loop in ``get_modules_from_all_apps``.
	"""
	original = _frappe_modules.get_modules_from_app
	if getattr(original, "_ee_none_safe", False):
		return  # already applied in this process

	@functools.wraps(original)  # carries redis_cache's .clear_cache / .ttl across
	def get_modules_from_app(app):
		return original(app) or []

	get_modules_from_app._ee_none_safe = True
	_frappe_modules.get_modules_from_app = get_modules_from_app


#: Extensions a browser will execute, or can be talked into executing, if the response says
#: ``Content-Disposition: inline``. Taken verbatim from Frappe's own ``develop`` branch, which
#: widened its v16 list of four to these fourteen. Everything here is HTML, XML-with-a-stylesheet,
#: or Flash — formats whose defining property is that the *content* decides what runs.
#:
#: ``.svg`` and ``.svgz`` are on it because an SVG is an image that runs script. That surprises
#: people, and it is the entry most likely to be removed by somebody tidying an image format out
#: of a list of documents.
_EXECUTABLE_ON_VIEW_EXTENSIONS = (
    ".svg",
    ".svgz",
    ".html",
    ".htm",
    ".xhtml",
    ".xht",
    ".shtml",
    ".shtm",
    ".mhtml",
    ".mht",
    ".xml",
    ".xsl",
    ".xslt",
    ".swf",
)


def _patch_private_files_are_never_served_inline():
    """Force-download every executable attachment type, and stop content-type sniffing.

    **The finding.** Frappe v16 force-downloads exactly four extensions from the
    ``/private/files/…`` route — ``.svg``, ``.html``, ``.htm``, ``.xml`` — and serves everything
    else ``inline``. Frappe's ``develop`` branch widened that list to fourteen. The seven in the
    gap (``.xhtml``, ``.svgz``, ``.shtml``, ``.mhtml``, ``.xsl``, ``.xslt``, ``.swf``) are served
    from **our own origin** with a scriptable ``Content-Type`` and ``Content-Disposition:
    inline``, which is stored XSS with the viewer's session cookies attached. Anyone who can
    attach a file to anything — a chat message, a Project, a Contract — can upload one.

    ``nosniff`` is set for the case the list cannot cover: a file whose extension is unremarkable
    but whose *bytes* are HTML. Without it a browser is free to sniff past a declared
    ``text/plain`` and render markup. Frappe sets this header nowhere in v16.

    **Why a monkeypatch and not an nginx rule.** ``infra/configs/startup_script.sh`` runs
    ``bench setup production`` on every boot, which regenerates the nginx config from bench's
    own template — a hand-edited rule is erased at the next reboot. This is fifteen lines that
    survive ``bench update`` by living here, which is what this module is for.

    **What it deliberately does not fix.** *Public* files (``is_private = 0``) never reach Python
    at all: nginx serves them straight off disk with ``try_files``. No application code can
    defend them, and the only control is nginx's own four-extension regex. That is the argument
    for chat attachments being ``is_private = 1`` without exception, which
    ``chat/api/compose.py`` already enforces server-side.

    One further trap, recorded because it is invisible and points the wrong way: nginx's
    ``add_header`` inheritance is all-or-nothing per level, so the ``/private/files/*`` location
    that defines its own header **drops** the server-level ``nosniff``, ``X-Frame-Options`` and
    HSTS. The responses that most need hardening are the ones that lose it — which is the second
    reason to set the header here, where it rides on the upstream response nginx forwards.
    """
    import frappe.utils.response as _frappe_response

    # The tuple is read as a module global inside `send_private_file` on every call, so widening
    # it here is enough — no wrapper needed for the disposition half.
    for extension in _EXECUTABLE_ON_VIEW_EXTENSIONS:
        if extension not in _frappe_response.FORCE_DOWNLOAD_EXTENSIONS:
            _frappe_response.FORCE_DOWNLOAD_EXTENSIONS += (extension,)

    original = _frappe_response.send_private_file
    if getattr(original, "_ee_nosniff", False):
        return  # already applied in this process

    @functools.wraps(original)
    def send_private_file(*args, **kwargs):
        response = original(*args, **kwargs)
        # `setdefault`, not assignment: if a future Frappe sets this itself, ours must not be
        # the value that wins — a patch that silently overrides an upstream fix is how the
        # upstream fix gets blamed for behaviour it does not have.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    send_private_file._ee_nosniff = True
    _frappe_response.send_private_file = send_private_file


_PATCHES = (
    _patch_get_modules_from_app_none_safe,
    _patch_private_files_are_never_served_inline,
)


def apply():
	"""Apply every runtime monkeypatch. Idempotent; safe to call repeatedly.

	A failing patch is logged and skipped, never raised — this runs while Frappe
	is importing ``hooks.py``, and an exception here would break hook loading for
	the whole app.
	"""
	for patch in _PATCHES:
		try:
			patch()
		except Exception:
			try:
				frappe.logger("erpnext_enhancements").warning(
					f"Failed to apply monkeypatch {patch.__name__!r}", exc_info=True
				)
			except Exception:
				pass
