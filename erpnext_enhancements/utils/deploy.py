"""Per-deploy cache-bust token, shared by the standalone PWAs (/kiosk, /wall).

Extracted from ``www/kiosk.py`` (v1.13.0) so both shells version their assets
and service workers off the same token.
"""

import os

import frappe

import erpnext_enhancements


def get_deploy_version() -> str:
	"""A token that changes on every deploy, used to cache-bust the PWAs.

	``sites/assets/assets.json`` is rewritten by every ``bench build`` — it is
	the same file Frappe's own ``frappe.utils.get_build_version`` reads — so
	its mtime fingerprints the deploy. The PWAs need it because raw
	``/assets`` files are served with a 1-year *immutable* Cache-Control (the
	v0.8.1 stale-cache bug): the shells append ``?v=<token>`` to their asset
	URLs so each deploy is a brand-new URL to the browser, and each service
	worker is registered as ``/<name>-sw.js?v=<token>`` so it keys its cache
	on the deploy.

	Unlike frappe's helper (which falls back to a *random* string and would
	re-bust on every page view), this falls back to the app version so the
	token stays stable between deploys.
	"""
	try:
		return str(int(os.path.getmtime(os.path.join(frappe.local.sites_path, "assets", "assets.json"))))
	except OSError:
		return erpnext_enhancements.__version__
