"""One module per network, each exposing ``publish(context, transport) -> {"external_post_id", "permalink"}``.

**None is installed yet.** TASK-2026-01483 (Facebook, Instagram), 01484 (LinkedIn) and 01485
(YouTube) each add one line to ``MODULES``. Until a network has a publisher it is not
*sendable*: its jobs stay Pending and untouched, exactly as if its switch were off, so a post
approved early simply waits.

``context`` is the job, the Social Post, its target row, the Social Account and the media, as
plain dicts (``sweeper.publish_context``). A publisher raises ``MarketingAPIError`` with the
HTTP status on failure and never retries a write itself -- the outbox decides
(``outbox.classify_failure``).
"""

import importlib

#: network -> dotted module path. Empty until the publishers ship.
MODULES = {}


def publisher_for(network):
	"""The publisher module for ``network``, or None when none is installed."""
	path = MODULES.get(network)
	return importlib.import_module(path) if path else None
