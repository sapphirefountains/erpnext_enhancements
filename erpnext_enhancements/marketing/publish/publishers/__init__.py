"""One module per network. The contract, two phases, and why there are two:

* ``prepare(context, transport) -> send``: everything that makes **nothing public**. Checking the
  post, resolving media URLs, creating Instagram's media containers and waiting for a video to
  process, uploading Facebook photos unpublished. It raises ``MarketingAPIError`` (with the HTTP
  status) on a network failure, and the outbox **retries** a failure here freely, because nothing
  was published.
* ``send() -> {"external_post_id", "permalink", "warning"?}``: the one step that makes the post
  public -- Instagram's ``media_publish``, Facebook's feed or photo post -- plus what follows it
  (reading the permalink, the first comment). The outbox records ``dispatched_at`` before calling it,
  so a failure here that might have published (a timeout, a 5xx) becomes **Unconfirmed**, never an
  automatic resend. Anything after the public step that fails (the first comment, the permalink)
  must not raise: the post is live, so send() returns success with a ``warning``.

A publisher never retries a write itself; the outbox decides (``outbox.classify_failure``). A
quota refusal that a network reports as something other than 429 (YouTube's ``quotaExceeded`` is a
403) must be re-raised as status 429, so the job waits instead of failing.

``context`` is the job, the Social Post, its target row, the Social Account and the media, as plain
dicts (``sweeper.publish_context``).

A network with no module here is not *sendable*: its jobs stay Pending and untouched, exactly as if
its switch were off.
"""

import importlib

#: network -> dotted module path.
MODULES = {
	"Facebook": "erpnext_enhancements.marketing.publish.publishers.meta",
	"Instagram": "erpnext_enhancements.marketing.publish.publishers.meta",
	"LinkedIn": "erpnext_enhancements.marketing.publish.publishers.linkedin",
}


def publisher_for(network):
	"""The publisher module for ``network``, or None when none is installed."""
	path = MODULES.get(network)
	return importlib.import_module(path) if path else None
