"""Organic publishing to Facebook, Instagram, LinkedIn and YouTube (Phase 2, TASK-2026-01462).

The one part of this module that writes to the outside world, so three rules hold here that
the read-only ad connectors never needed:

* **Approval first.** A post reaches a network only after a named approver signs it off
  (decision 9). The switches in ``gate.py`` decide whether an *approved* post may leave; no
  switch replaces the approval.
* **Nothing here can touch spend** (decision 3). Publishing writes posts, never ads: no
  spend-capable OAuth scope may be requested anywhere in ``marketing/``, which
  ``tests/test_marketing_publishing.py`` enforces. Meta's ``pages_manage_ads`` is the one to
  watch, because it would let a post be boosted into paid spend.
* **Never the ad connectors' transport.** ``core/client.py`` refuses every Meta and LinkedIn
  POST by design. Publishing gets its own allowlist when its publishers are built
  (TASK-2026-01483 to 01485). The ads one does not grow a write.
"""
