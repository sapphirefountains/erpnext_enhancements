# WI-011 — Apply Runbook (role cleanup)

Companion to [`wi011-access-matrix.md`](wi011-access-matrix.md). This is the exact, ordered change set to bring prod in line with the matrix, split by risk. **Group A is safe to apply now; Group B changes who can approve payments / hold admin and needs the CEO's sign-off.**

## Application status (2026-07-24) — ✅ complete

- **Group A — ✅ applied.** parker.bailey via API; brian.morisseau / richard.hansen / lian.silva in Desk. Verified: none of the four holds Accounts Manager or Accounts User.
- **Group B — ✅ applied** (by admin). Verified SoD: **Lisa Symanski = Accounts User (preparer), NOT Accounts Manager**; **James Harris = Accounts Manager (approver)** → preparer ≠ approver holds. `billing@` disabled.
- **Decisions:** `triton@` kept as-is (active service account); `shellycekeyes@gmail` + `kendalyn.harris` **retained** (future users, not disabled).
- **✅ Resolved:** `john@skylinepathway.com` (CPA) had `PO Approver` removed. Verified: `PO Approver` holders are now **James Harris + Nikolas Bradshaw only**. WI-011 role cleanup is complete.

## Mechanism & API limitation (verified 2026-07-24)

- This site uses the single **`User.role_profile_name`** field (no multi `role_profiles`).
- **Role changes must be made in the Desk UI, not the API.** MCP `update_document` can only edit `User.roles` when the user has **no** Role Profile assigned; while a profile is set, role edits are **silently discarded** on save and the profile field **cannot be cleared** via the API (both empty-string and `null` updates report success but do nothing). Reserve the API for reads.
- The lean profiles (`Sales`, `Purchase`, …) **omit the employee baseline** (`Employee`, `Employee Self Service`, desk access), and some profiles are **bloated** (`Sales Team`/`Finance Team` include `Accounts Manager`; `Design Team`/`Production Team` include `Purchase Master Manager`). So the safe cleanup is to **uncheck the specific over-granted roles**, not reset a user to a profile. Full profile de-bloating is a Phase-2 WI-010 follow-up.

**Rollback snapshot** of every affected user (`role_profile_name` + `roles[]`) was captured before applying; role changes are non-destructive (no document/ledger impact).

## Group A — remove over-granted finance/admin roles (in Desk)

For each user below: open `Users & Permissions → User → <person>`, **clear the Role Profile field** if set, **uncheck the listed roles**, and Save.

| User | Clear profile | Uncheck roles |
|---|---|---|
| parker.bailey (Purchasing Agent) | (was null) | ✅ **DONE** — System Manager, Script Manager, Accounts Manager, Accounts User, Accounts Receivable, Auditor, Finance Team, Executive Team, HR Manager |
| brian.morisseau (Sales Rep) | `Sales` (stray) | Accounts Manager, Accounts User |
| richard.hansen (Marketing) | `Sales Team` | Accounts Manager, Accounts User |
| lian.silva (Junior Tech) | `Production Team` | Accounts User (stray) |

Unchecking `Accounts Manager` removes payment-approval (Payment Entry submit) — the SoD goal. No change needed for `clegg.mabey` / `austin.healey` (no finance/admin roles) or the designers / other technicians; their broad-but-benign role sets are a Phase-2 profile trim, not a security risk.

## Group B — needs CEO sign-off (changes approval authority / admin)

1. **Lisa Symanski — make her the AP/AR *preparer* only.** Remove the `Finance Team` profile; set direct roles: `Accounts User`, `Purchase Manager`, `Purchase User`, `Stock User`, `Employee`, `Employee Self Service`, `System User`. **Removes `Accounts Manager`** so she can enter but not approve payments.
2. **James Harris (CEO) — the *approver*.** Keep `Accounts Manager` + `System Manager` + `PO Approver`. (Confirms preparer ≠ approver: Lisa prepares, James approves.)
3. **Nikolas Bradshaw — sys-admin.** Keep `System Manager` + `PO Approver`.
4. **John Juntunen (CPA) — keep access; NOT `PO Approver`** (already true). *Per direction, John retains broad access.* **Optional tightening (recommended for an external party):** swap the `System Manager` profile for a scoped set — `Auditor` (read-all) + `Accounts Manager` + `Accounts User` + `Report Manager` + `Analytics` — which removes `Script Manager` (run server code) and user-management while keeping full accounting/reporting visibility. Decision: keep-as-is vs scope-down.
5. **Service / uncertain accounts** (decisions 2026-07-24):
   - `triton@sapphirefountains.com` — **kept as-is** (active service account, 72 roles; per direction, not scoped).
   - `poseidon@sapphirefountains.com` — active service account (used today, 12 roles); leave as-is, revisit in a Phase-2 review if desired.
   - `shellycekeyes@gmail.com` + `kendalyn.harris@sapphirefountains.com` — **retained** (future users; not disabled, per direction).
   - `billing@sapphirefountains.com` — disabled ✅.

## Acceptance check after apply

- `enabled=1 System User` with null `role_profile_name` = 0 — **note:** Lisa/James/Nik/John are intentionally direct-role-managed (they need role sets no single profile provides), so if the criterion is read strictly as "every enabled user has a profile," either assign them a base profile + document the direct additions, or record them as approved direct-managed exceptions. **CEO to choose** which reading applies.
- ≥1 `Accounts Manager` who is not the daily preparer → James (approver) ≠ Lisa (preparer). ✅ once Group B lands.
- Kiosk `Employee.user_id` for the 15 active employees → already set. ✅

## Rollback

Restore each changed user's `role_profile_name` + `roles[]` from the pre-apply snapshot. Role changes are non-destructive to data; no document or ledger impact.
