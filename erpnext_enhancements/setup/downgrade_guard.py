"""Refuse a migrate that runs an OLDER build of this app over a site a newer one has migrated.

Registered first in ``before_migrate`` (hooks.py), so it runs in Frappe's ``pre_schema_updates``:
before both patch phases, before model sync and before fixture sync. The site's
``Installed Application`` row still holds the version that last migrated it there; v16 updates
it only afterwards, in ``post_schema_updates`` (``InstalledApplications.update_versions``).

Why this exists
---------------
Fixture sync is additive. Removing a Custom Field from ``fixtures/custom_field.json`` only stops
managing it; the record that deletes it is a one-shot patch, which runs once and is then in
``tabPatch Log`` for good. So a migrate that runs an older build re-imports that build's fixtures,
and every customization a later release retired comes back. No patch will ever delete it again,
because the patches that did are already logged.

That happened once. Between 18:07 and 18:08 on 2026-07-18, the evening the site moved to the new
Google Cloud infrastructure, a migrate ran a build from before v1.159.0 (whose fixtures still had
these records, ``module`` empty, with the same ``insert_after`` as on prod) and re-inserted 14
Custom Fields:

- the 12 blank duplicate "Comments" Tab Breaks v1.159.2 removed (Address, Batch, Delivery Note,
  Lead, Project, Quotation, Serial No, Stock Entry and Task's ``custom_comments_tab``; Employee,
  Purchase Order and Supplier Quotation's ``custom_comments``);
- Opportunity's ``custom_lost_reason`` and ``custom_lost_competitor``, which v1.159.0 replaced with
  ERPNext's own ``lost_reasons`` and ``competitors``.

They stayed for three months: blank tabs on 12 forms, and a second Lost Reason on Opportunity that
four deals were given a value in. v1.682.1 deleted them again
(``patches/retire_resurrected_customizations``); this guard stops the cause.

Every release bumps ``__version__`` (CLAUDE.md), and main only moves forward, so on this pipeline a
build older than the site is always a mistake: a stale image, a branch deployed by hand, two
deploys finishing out of order. An emergency rollback ships as a revert commit with a NEW version.
If an older build genuinely must run, set ``allow_erpnext_enhancements_downgrade: 1`` in the
site's ``site_config.json`` for that one migrate.

Never blocks on its own failure: an unreadable version lets the migrate run.
"""

import frappe

APP = "erpnext_enhancements"
OVERRIDE_KEY = "allow_erpnext_enhancements_downgrade"


def parse(version):
	"""``"1.682.1"`` -> ``(1, 682, 1)``; a missing or odd part reads as 0, a ``v`` prefix is ignored."""
	parts = []
	for piece in str(version or "").strip().lstrip("vV").split(".")[:3]:
		digits = ""
		for char in piece:
			if not char.isdigit():
				break
			digits += char
		parts.append(int(digits) if digits else 0)
	return tuple([*parts, 0, 0, 0][:3])


def installed_version():
	"""The version that last migrated this site, or None (a fresh install has no row yet)."""
	rows = frappe.db.sql(
		"select app_version from `tabInstalled Application` where app_name = %s limit 1",
		(APP,),
	)
	return (rows[0][0] if rows else None) or None


def code_version():
	from erpnext_enhancements import __version__

	return __version__


def check(code, installed, allowed=False):
	"""None when the migrate may run, else the message to refuse it with."""
	if not installed or not code:
		return None
	if parse(code) >= parse(installed):
		return None
	if allowed:
		return None
	return (
		f"This build of {APP} is v{code}, but v{installed} last migrated this site. Migrating an "
		"older build re-imports its old fixtures and brings back customizations later releases "
		"retired; on 2026-07-18 it brought back 14 Custom Fields that stayed for three months. "
		f"Deploy a build at v{installed} or newer. If an older build genuinely must run, set "
		f'"{OVERRIDE_KEY}": 1 in this site\'s site_config.json for that one migrate.'
	)


def refuse_downgrade():
	"""``before_migrate``: raise when this build is older than the one that last migrated the site."""
	try:
		code = code_version()
		installed = installed_version()
		allowed = bool(frappe.conf.get(OVERRIDE_KEY))
	except Exception:
		# The guard must never be the thing that breaks a deploy.
		return
	message = check(code, installed, allowed)
	if message:
		raise frappe.ValidationError(message)
	if allowed and installed and parse(code) < parse(installed):
		print(f"{APP}: migrating v{code} over v{installed} because {OVERRIDE_KEY} is set.")
