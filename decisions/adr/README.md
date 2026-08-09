# Architecture decision records

**These are engineering decisions.** The business decisions live next door in
[`../OPEN-DECISIONS.md`](../OPEN-DECISIONS.md) as the `OD-n` register, and the two must not
be confused: an OD is something the business decides and no work item resolves; an ADR is
something we decided and are accountable for.

Module READMEs describe **what the code does**. These records describe **why it is built that
way**, for the choices that are expensive to reverse or surprising to inherit.

Most were written retroactively, from rationale already present in the code, `CHANGELOG.md`,
`PLAN.md`, and the CI configuration. They are collected here because that reasoning was
scattered across two dozen files — and because the recurring failure mode in this repository
is someone seeing a deliberate construct, assuming it is an oversight, and "fixing" it.

## When to add one

Add an ADR when a choice is expensive to reverse or surprising to inherit. A new endpoint
following the existing pattern does not need one. A decision about how customizations are
version-controlled, what CI gates on, or why a dependency is deliberately absent does.

Copy [`0000-template.md`](0000-template.md), take the next number, link it below. An ADR is
immutable once accepted — revisiting a decision means a new record that supersedes the old
one, so the history of the reasoning survives.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-native-first.md) | Native ERPNext first; a custom mechanism where a native one suffices is a defect | Accepted |
| [0003](0003-repo-is-source-of-truth-for-customizations.md) | The repo, not the site, is the source of truth for customizations | Accepted |
| [0004](0004-no-vendor-sdks.md) | Integrations are hand-rolled on `requests`; no vendor SDKs | Accepted (forced) |
| [0005](0005-bench-free-tests-in-ci.md) | CI runs only bench-free tests | Accepted, with a known gap |
| [0006](0006-ai-writes-need-desk-confirmation.md) | AI writes are confirmed in the desk, never by the model | Accepted |
| [0007](0007-tolerate-mixed-indentation.md) | Tolerate mixed indentation rather than reformat | Accepted, temporary |
| [0008](0008-global-assets-ship-as-bundles.md) | Global browser assets ship as esbuild bundles, not raw `/assets` paths | Accepted |
| [0009](0009-erpnext-google-chat-triton.md) | Employee chat is a module in this app, mirrored to Google Chat, with Triton on `@triton` | Accepted |
