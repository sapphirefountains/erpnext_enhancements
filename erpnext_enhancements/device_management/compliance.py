"""Pure device-lifecycle and compliance rules — no frappe dependency.

Kept frappe-free on purpose so it unit-tests bench-free (the house pattern of
``api/integrations_health.py``'s tone helpers — see
``tests/test_device_management.py``). The Managed Device controller
(``doctype/managed_device/managed_device.py``) and the device API
(``api/device_management.py``) import the constants and helpers from here so the
rules live in exactly one place.

Phase 2 (the ``mdm_integration`` provider layer) overwrites a device's posture
from a live provider feed; until then posture is self-attested and
``derive_compliance`` is the single rule that turns it into a status.
"""

# Device lifecycle states. The first is the default for a freshly enrolled
# device; "Retired" is terminal.
STATUSES = ["In Stock", "Assigned", "In Repair", "Lost/Stolen", "Retired"]

# Allowed status transitions, enforced in ManagedDevice.validate via
# ``is_valid_transition``. A device only ever holds a current assignee while
# "Assigned"; every other state clears it (the history child table retains who
# had it). Recovery of a lost device routes back through "In Stock".
ALLOWED_TRANSITIONS = {
	"In Stock": {"Assigned", "In Repair", "Lost/Stolen", "Retired"},
	"Assigned": {"In Stock", "In Repair", "Lost/Stolen", "Retired"},
	"In Repair": {"In Stock", "Assigned", "Lost/Stolen", "Retired"},
	"Lost/Stolen": {"In Stock", "Retired"},
	"Retired": set(),
}

# The states in which a device legitimately has a current assignee.
ASSIGNED_STATES = {"Assigned"}


def is_valid_transition(old, new):
	"""True if a device may move from ``old`` status to ``new``.

	A no-op (``old == new``) is always allowed; an unknown ``old`` permits any
	move so a hand-corrected/legacy row is never wedged.
	"""
	if old == new:
		return True
	if old not in ALLOWED_TRANSITIONS:
		return True
	return new in ALLOWED_TRANSITIONS[old]


def derive_compliance(screen_lock_enabled, encryption_enabled):
	"""Resolve a compliance status from the two attested posture booleans.

	Compliant only when the device both locks its screen and encrypts storage;
	anything else is Non-Compliant. (OS-version floors are a Phase-2 provider
	concern.) This is the one rule the self-service attestation and any future
	provider sync both funnel through.
	"""
	return "Compliant" if (screen_lock_enabled and encryption_enabled) else "Non-Compliant"
