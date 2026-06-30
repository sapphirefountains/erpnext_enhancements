# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feed for the Astrology widget (daily horoscope, pick-a-sign).

Fetches the daily horoscope server-side (avoids browser CORS) from a free public
API — no key — and caches it per sign per day. Defensive: any upstream failure
returns ``{"enabled": True, "text": None, "reason": ...}`` so the widget shows a
graceful notice rather than erroring the board.
"""

import frappe
from frappe.utils import nowdate

from erpnext_enhancements.api.finance_dashboard import _require_finance, _settings, _widget_enabled

DEFAULT_API_BASE = "https://horoscope-app-api.vercel.app"
CACHE_TTL_SECONDS = 6 * 3600  # refresh a few times a day at most

ZODIAC_SIGNS = (
	"Aries",
	"Taurus",
	"Gemini",
	"Cancer",
	"Leo",
	"Virgo",
	"Libra",
	"Scorpio",
	"Sagittarius",
	"Capricorn",
	"Aquarius",
	"Pisces",
)
_SIGN_LOOKUP = {s.lower(): s for s in ZODIAC_SIGNS}


@frappe.whitelist()
def get_horoscope(sign):
	"""Daily horoscope for ``sign`` (one of the 12 zodiac names, case-insensitive)."""
	_require_finance()
	settings = _settings()
	if not _widget_enabled("finance_astrology_enabled", settings):
		return {"enabled": False}

	canonical = _SIGN_LOOKUP.get((sign or "").strip().lower())
	if not canonical:
		return {"enabled": True, "text": None, "reason": "Pick a zodiac sign."}

	today = nowdate()
	cache_key = f"ee_horoscope::{canonical}::{today}"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return {"enabled": True, "sign": canonical, "date": today, "text": cached, "cached": True}

	base = (settings.get("horoscope_api_base") or DEFAULT_API_BASE).rstrip("/")
	url = f"{base}/api/v1/get-horoscope/daily"
	try:
		import requests

		response = requests.get(url, params={"sign": canonical, "day": "TODAY"}, timeout=15)
		if response.status_code >= 400:
			raise ValueError(f"status {response.status_code}")
		payload = response.json() or {}
		text = ((payload.get("data") or {}).get("horoscope_data") or "").strip()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Horoscope fetch failed")
		return {"enabled": True, "sign": canonical, "text": None, "reason": "Horoscope service unavailable."}

	if not text:
		return {"enabled": True, "sign": canonical, "text": None, "reason": "No horoscope available."}

	frappe.cache().set_value(cache_key, text, expires_in_sec=CACHE_TTL_SECONDS)
	return {"enabled": True, "sign": canonical, "date": today, "text": text, "source": base}
