# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The publishing networks and the Marketing Settings switch for each (TASK-2026-01479).

The four networks are decision 2 (2026-08-13): Facebook, Instagram, LinkedIn and YouTube,
deliberately excluding X (paid API tier), TikTok (audit) and Pinterest.

They are named apart from the ad platforms in ``core/constants.py`` even where the vendor is
the same. *Meta Ads* and *Facebook* are one company, but reading a campaign's spend and
posting to the Page are different permissions, reviewed separately and switched separately,
so a switch, a credential prefix or a log line must never be able to mean both.
``tests/test_marketing_publishing.py`` keeps the two sets disjoint.
"""

NETWORK_FACEBOOK = "Facebook"
NETWORK_INSTAGRAM = "Instagram"
NETWORK_LINKEDIN = "LinkedIn"
NETWORK_YOUTUBE = "YouTube"
PUBLISH_NETWORKS = (NETWORK_FACEBOOK, NETWORK_INSTAGRAM, NETWORK_LINKEDIN, NETWORK_YOUTUBE)

#: Per-network publishing switch on Marketing Settings. Each ships ``0``.
PUBLISH_FLAG = {
	NETWORK_FACEBOOK: "facebook_publishing_enabled",
	NETWORK_INSTAGRAM: "instagram_publishing_enabled",
	NETWORK_LINKEDIN: "linkedin_publishing_enabled",
	NETWORK_YOUTUBE: "youtube_publishing_enabled",
}
