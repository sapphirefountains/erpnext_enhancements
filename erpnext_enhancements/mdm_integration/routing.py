"""Pure provider-routing and action-policy rules — no frappe dependency.

Kept frappe-free so the device-class routing, the per-provider capability map,
and (most importantly) the BYOD wipe-mode guard and the Miradore selective-wipe
guard unit-test bench-free (``tests/test_mdm_integration.py``). ``client.py`` and ``actions.py`` import
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


# ---------------------------------------------------------------------------
# Miradore: what a "selective" wipe can safely mean
# ---------------------------------------------------------------------------
#
# Miradore has no selective-wipe option. POST /api/v2/Device/{id}/Wipe takes a
# WipeConfiguration with no such field (``additionalProperties: false``), and what
# it does depends on how the device is enrolled, not on anything we send: a fully
# managed Android device is factory-reset, a work-profile Android device loses
# only its work profile, and an iPhone is erased. The operation that removes
# company data and leaves personal data alone is Retire (DELETE
# /api/v2/Device/{id}), which also unenrolls the device -- and even Retire
# factory-resets fully managed Android devices and Shared iPads (Miradore KB
# "Retire a device", checked 2026-09-22). So a selective wipe is a Retire, allowed
# only on the enrollment types for which Miradore says Retire keeps personal data.
# The ``Client.ManagementType`` values are from Miradore API spec v1.19, Appendix 2.
MIRADORE_RETIRE_KEEPS_PERSONAL_DATA = frozenset({"AndroidProfileOwner", "iOSUnsupervised", "iOSSupervised"})


def miradore_selective_wipe_refusal(management_type, model=None):
	"""Why a selective wipe (a Retire) must not run on this Miradore device, or None.

	Fails closed: an unknown or unlisted management type is refused, because the
	cost of guessing wrong is a factory reset of somebody's personal phone.
	"""
	kind = (management_type or "").strip()
	if kind == "AndroidDeviceOwner":
		return (
			"This is a fully managed Android device. Miradore cannot remove only company data from "
			"it: Retire and Wipe both factory-reset it. Use a full wipe if that is what you intend."
		)
	if kind not in MIRADORE_RETIRE_KEEPS_PERSONAL_DATA:
		return (
			f"Miradore reports management type '{kind or 'unknown'}' for this device and does not "
			"document that Retire keeps personal data on it, so a selective wipe is refused."
		)
	if kind == "iOSSupervised" and "ipad" in (model or "").lower():
		return (
			"This is a supervised iPad, which may be a Shared iPad, and Retire factory-resets Shared "
			"iPads. A selective wipe is refused; use a full wipe if that is what you intend."
		)
	return None


# ---------------------------------------------------------------------------
# Action1: where and how a script runs
# ---------------------------------------------------------------------------


def action1_script_target(platform):
	"""``(platform, language)`` for Action1's run_script template, from a device platform.

	The template's ``platform`` enum is Windows / Mac / Linux, and its manual-script
	languages are PowerShell / Command / Bash. Windows gets PowerShell; everything
	else Bash. Returns ``(None, None)`` for a platform Action1 cannot script.
	"""
	text = (platform or "").strip().lower()
	if text.startswith("win"):
		return "Windows", "PowerShell"
	if "mac" in text or "darwin" in text or "osx" in text:
		return "Mac", "Bash"
	if "linux" in text:
		return "Linux", "Bash"
	return None, None
