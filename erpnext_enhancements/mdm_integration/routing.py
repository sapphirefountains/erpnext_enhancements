"""Pure provider-routing and action-policy rules — no frappe dependency.

Kept frappe-free so the device-class routing, the per-provider capability map,
and (most importantly) the BYOD wipe-mode guard unit-test bench-free
(``tests/test_mdm_integration.py``). ``client.py`` and ``actions.py`` import
these so the rules live in one place.
"""

# Which provider manages a device, by device class. Miradore = mobile MDM,
# Action1 = computer RMM. device_type is primary; platform is the fallback.
_TYPE_TO_PROVIDER = {
	"Phone": "Miradore",
	"Tablet": "Miradore",
	"Laptop": "Action1",
	"Desktop": "Action1",
}
_PLATFORM_TO_PROVIDER = {
	"Android": "Miradore",
	"iOS": "Miradore",
	"iPadOS": "Miradore",
	"Windows": "Action1",
	"macOS": "Action1",
	"Linux": "Action1",
}

# HTTP statuses a scheduled retry can never fix on its own — the operator has to
# change the configuration (bad/expired key, revoked permission, wrong org/path).
# The sync layer stops retrying these and *pauses* the provider until its
# credentials are re-saved, instead of hammering the provider API (and the Error
# Log) every cycle. Everything else — 5xx, 429 rate-limits, network timeouts (no
# status at all) — is treated as transient and stays retryable.
NON_RETRYABLE_STATUSES = {400, 401, 403, 404}


def is_retryable_status(status_code):
	"""False for permanent provider failures (bad-request/auth/permission/not-found)
	that re-running on a schedule cannot fix; True for transient ones and for
	``None`` (a network/transport error that carried no HTTP status)."""
	return status_code not in NON_RETRYABLE_STATUSES


# What each provider's API can actually do. The executor rejects any action a
# provider's set does not contain (so "wipe" can never reach an Action1 computer).
CAPABILITIES = {
	"Miradore": {"list", "get", "lock", "wipe", "locate"},
	"Action1": {"list", "get", "reboot", "run_script", "deploy_patch"},
	# Mock can do everything so the whole pipeline is exercisable without keys.
	"Mock": {"list", "get", "lock", "wipe", "locate", "reboot", "run_script", "deploy_patch"},
}


def provider_key_for_device(device_type, platform):
	"""Return the provider key ('Miradore'/'Action1') for a device, or None.

	Phone/Tablet → Miradore, Laptop/Desktop → Action1; if device_type is unset
	or 'Other', fall back to the platform mapping.
	"""
	provider = _TYPE_TO_PROVIDER.get((device_type or "").strip())
	if provider:
		return provider
	return _PLATFORM_TO_PROVIDER.get((platform or "").strip())


def provider_supports(provider_key, action):
	"""True if ``provider_key`` can perform ``action`` (per CAPABILITIES)."""
	return action in CAPABILITIES.get(provider_key, set())


def resolve_wipe_mode(ownership, requested_mode, *, block_byod_full=True, allow_corporate_full=True):
	"""Resolve the effective wipe mode and any policy error, BEFORE the API call.

	Returns ``(effective_mode, error)``. The invariant is **BYOD never full-wipes**:
	a BYOD device is always coerced to a selective (corporate-data-only) wipe, and
	an explicit full-wipe request on BYOD is refused when ``block_byod_full`` is on.
	A company full wipe is refused when ``allow_corporate_full`` is off.
	"""
	mode = (requested_mode or "selective").strip().lower()
	if ownership == "BYOD":
		if mode == "full" and block_byod_full:
			return None, "Full wipe of a BYOD (personally owned) device is not permitted — only a selective wipe."
		return "selective", None
	# Company-owned
	if mode == "full" and not allow_corporate_full:
		return None, "Full wipe of company devices is disabled in MDM Settings."
	return mode, None
