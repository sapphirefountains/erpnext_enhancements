"""Global document-change notifier for the Triton AI assistant — RETIRED.

``global_triton_sync`` was registered on ``doc_events["*"]["after_save"]`` to POST a
"this doctype/name changed" webhook to Triton on every save site-wide. It never worked
and could not have, for three independent reasons, so it was removed in v1.341.1:

1. **Wrong event.** Frappe dispatches no server-side ``after_save`` document event
   (``run_post_save_methods`` runs ``on_update`` / ``on_submit`` / ``on_change`` / …).
   A ``doc_event`` under a name ``run_method`` never calls is simply never invoked, so
   the hook was inert from the day it was written — which is why the several patch
   docstrings that reason about "firing the global Triton after_save 142 times" were
   costing exactly zero.
2. **No authentication.** Triton's ``/api/v1/webhooks/frappe-webhook`` requires
   ``Authorization: Bearer <ERPNEXT_GATEWAY_SECRET>`` (it is a write primitive into a
   user's private RAG corpus). This sender attached no header, so every call would have
   401'd even with the event fixed.
3. **No real user mapping.** The endpoint ingests the named document into the *Triton
   ``user_id``'s* private corpus; this sender hard-coded ``user_id: 1``, which is
   meaningless — a global save hook has no per-user corpus to name.

Triton keeps its index fresh through its own sync engine (which re-reads ERPNext), so
nothing depended on this push. If a real-time push is ever wanted, it needs a genuine
design: the shared gateway secret on the header, a per-document → owning-user mapping
(or a system-corpus target), an HTTP timeout, and ``enqueue_after_commit=True`` so the
webhook never announces a save that later rolls back. Until then there is deliberately
no code here to switch on by accident.
"""
