# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

This repository documents itself unusually well. Every module has a README, `hooks.py` is
annotated, `patches/README.md` explains each patch, and the `CHANGELOG.md` entries are long
and explanatory on purpose.

What it lacked was a place for reasoning that belongs to no single module. The explanation
for why there is no Stripe SDK lives in a comment in `pyproject.toml`. Why the lint job is
advisory lives in a comment in `ci.yml`. Why `seed_po_creator_role` must be a patch rather
than a fixture lives in a table row in `patches/README.md`. Each is in a sensible place, and
collectively they are unfindable.

The cost is specific and recurring: someone encounters a deliberate construct, reads it as an
oversight, and improves it. Adding the Stripe SDK, normalising a file's indentation, making
lint blocking, adding a `frappe.db.commit()` so partial progress survives — each of those
looks like an improvement and each undoes a decision.

`decisions/OPEN-DECISIONS.md` already existed but is a different thing: it tracks **business**
decisions the business must make.

## Decision

Engineering decisions that are expensive to reverse or surprising to inherit are recorded as
ADRs under `decisions/adr/`, numbered sequentially, following `0000-template.md`.

They sit alongside — and are explicitly distinguished from — the `OD-n` business register in
`decisions/OPEN-DECISIONS.md`.

An ADR is immutable once accepted. Revisiting means a new record superseding the old.

## Consequences

- Module READMEs stay descriptive. ADRs carry the cross-cutting rationale. Neither has to do
  both jobs.
- A decision recorded and then quietly abandoned is worse than no record, so the index in
  `decisions/adr/README.md` and the pointer in `docs/development.md` exist to keep the habit
  visible.
- The initial records were written retroactively from evidence already in the repository.
  They reconstruct reasoning rather than reporting it first-hand, and say so.
- This does not replace the work-item discipline. A `WI-xxx` file is a unit of *work* with
  acceptance criteria; an ADR is a *decision* with consequences. Some work items produce an
  ADR; most do not.
