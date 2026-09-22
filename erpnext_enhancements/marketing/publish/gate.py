# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whether an approved post may go out to a network: the publishing half of "dormant".

Two switches on Marketing Settings, and both must be on:

1. ``enabled``, the module's master switch, which also governs the ad connectors;
2. the network's own publishing switch (``publish/constants.PUBLISH_FLAG``).

The master switch alone publishes nothing, the same rule the ad connectors follow. The ad
switches play no part: turning on read-only reporting for Meta does not bring Facebook
publishing one checkbox closer.

**This is not the approval check, and it cannot stand in for one.** Nothing is queued to
publish until a post has an approver (decision 9, TASK-2026-01486). These switches answer a
different question: whether an approved post may leave at all. It is meant to be asked at
send time, not at approval time, so that switching a network off stops even the posts
already approved and scheduled.

Pure: takes anything with ``.get`` (the Settings document, or a dict in a test), so the
sweeper and the composer ask the same question the same way, and the bench-free CI tier can
check it without a site.
"""

from erpnext_enhancements.marketing.publish import constants as P


def _on(value):
	"""A Check value as stored or submitted: 1, "1", True -> on; anything else -> off."""
	try:
		return int(value or 0) > 0
	except (TypeError, ValueError):
		return False


def network_enabled(network, settings):
	"""True only when the master switch and ``network``'s own switch are both on."""
	flag = P.PUBLISH_FLAG.get(network)
	if not flag:
		return False
	return _on(settings.get("enabled")) and _on(settings.get(flag))


def enabled_networks(settings):
	"""The networks an approved post may go out to right now, in ``PUBLISH_NETWORKS`` order."""
	return [network for network in P.PUBLISH_NETWORKS if network_enabled(network, settings)]
