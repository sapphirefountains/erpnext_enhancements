# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Online contract signing.

A customer receives a tokenised link, reads the agreement on a public page, and
signs it — which executes the contract and lets the automation chain that
already exists (operational maintenance contract, scheduling, billing) fire
itself, instead of waiting for a staff member to notice a signed PDF and flip a
status by hand.

Layout:

* ``providers/`` — how a signature is *collected*. In-house today; a commercial
  provider is a new module plus one registry entry.
* ``lifecycle`` — everything that happens *after* collection, shared by every
  provider. Owns the one rule that must not be optimised away: the contract is
  flipped with a real ``doc.save()``.
* ``tokens`` — minting, hashing and shape-checking the bearer credential.
* ``render`` — the ``sig()`` helper that puts a signature into an agreement.
* ``api`` / ``portal`` — the desk and guest entry points.
* ``tasks`` — the daily expiry and delivery-retry sweeps.
"""
