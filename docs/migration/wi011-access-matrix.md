# WI-011 — Per-User Access Matrix & Segregation-of-Duties Design

**Status:** DRAFT for **HR confirmation** (employee↔user mapping) and **CEO sign-off** (this *is* the SoD design — WI-011 acceptance).
**Authored:** 2026-07-24 from a live prod audit. **Blocks:** WI-012, WI-021 (kiosk), WI-044 (approval workflows), WI-048.
**Applies via:** native `User.role_profile_name` + `Employee.user_id` (a Desk exercise — no code). **Nothing here is applied to prod until the CEO signs the target column.**

Role Profiles come from WI-010 (now fixtured). Assigning them is this item. Creating/changing profiles is **not** (WI-010).

---

> ⚠️ **Terminology — two different "Accounts" in this instance.** The **Customer** DocType is relabeled **"Accounts"** in this site's UI (CRM style; the underlying doctype is still `Customer`). That is **unrelated** to the **`Accounts Manager` / `Accounts User` roles** in this document. Those are core ERPNext **accounting** roles — verified on prod, they grant submit (post/approve) rights on **Payment Entry, Journal Entry, Sales/Purchase Invoice, and the GL `Account`** (and only *read* on Customer). They govern **who can approve payments and post to the ledger**, not who manages customers.
>
> This collision very likely **explains the over-provisioning below**: a Marketing Specialist and a Sales Rep were probably granted "Accounts Manager" on the reasonable assumption it meant "manages customer *Accounts*" — when it actually confers finance-approval authority. **Done (v1.169.2):** the ambiguous **`Accounts` Role Profile** is renamed to **`Accounting`** (rename patch + fixture; no user was assigned it, so no access changed). The role *labels* themselves (`Accounts Manager` / `Accounts User`) are core ERPNext and left as-is — the profile rename plus this glossary resolve the ambiguity.

## 1. Headline findings (why this matters)

1. **Massive over-provisioning of finance authority.** Beyond the CEO, **Accounts Manager** (the AP/AR *approver* role) is currently held by a Marketing Specialist (Rich Hansen), a Sales Rep (Brian Morisseau), the Purchasing Agent (Parker Bailey), a shared mailbox (`billing@`), an integration service account (`triton@`), and the AP/AR preparer herself (Lisa Symanski). None of those should approve payments.
2. **`billing@sapphirefountains.com` is a shared mailbox holding Accounts Manager + Purchase Master Manager + Purchase Manager.** A non-attributable shared login with full finance + purchasing authority was the single highest control risk. **✅ Disabled by admin (2026-07-24).**
3. **`Purchase Master Manager` is held by 15 users** (largely via the Design Team profile). That role can submit any PO at any amount — it is exactly the population WI-013's threshold rule must *not* exempt. WI-013 therefore uses the new **`PO Approver`** role (CEO-only), created by WI-010.
4. **Preparer = approver on AP/AR.** Lisa Symanski (the daily AP/AR preparer) holds **both** Accounts User and Accounts Manager, so she can enter and approve the same payment. See §3 for the structural fix.
5. **Kiosk precondition already met:** all 15 Active employees have `Employee.user_id` set to an enabled user — WI-021's Time Kiosk can resolve every field employee. WI-011's remaining job is `role_profile_name` + the SoD cleanup.

---

## 2. Target matrix — Active employees (15)

`Emp?` = has `Employee.user_id` (kiosk). **Target Role Profile** is the proposal; **Also grant / Remove** are direct-role deltas on top of the profile. HR confirms the mapping; CEO signs the target.

| User | Employee / Designation | Dept | Current profile | **Target Role Profile** | Direct-role change | CEO ✔ |
|---|---|---|---|---|---|---|
| james.harris | James Harris — CEO | Executive | *(none; direct)* | **Executive** | Keep **System Manager**, **PO Approver**, **Accounts Manager** (the approver) | |
| nikolas.bradshaw | Nikolas Bradshaw — Internal Systems Manager | Operations | *(none; direct)* | **System Manager** | Keep **PO Approver** (sys-admin — confirm vs strict CEO-only). Remove Accounts Manager | |
| parker.bailey | Parker Bailey — Purchasing Agent/Inventory Clerk | Operations | *(none; direct)* | **Purchase** (+ Inventory Clerk) | **Remove System Manager, Accounts Manager** | |
| lisa.symanski | Lisa Symanski — AP/AR, Purchasing Manager | Finance | Finance Team | **Accounts *preparer*** (Accounts User only) + Purchase Manager | **Remove Accounts Manager** (see §3) | |
| clegg.mabey | Clegg Mabey — Project Manager | Production | *(none; direct)* | **Projects & Operations** | +Purchase Manager/User for the WI-012 MR→PO PM role | |
| richard.hansen | Rich Hansen — Marketing Specialist | Marketing | Sales Team | **Sales & Marketing** | **Remove Accounts Manager, Accounts User** | |
| brian.morisseau | Brian Morisseau — Sales Representative | Sales | *(none; direct)* | **Sales** | **Remove Accounts Manager, Accounts User** | |
| daniel.blass | Daniel Blass — Electrical Designer | Design | Design Team | **Design Team** | (Design Team bundles Purchase Master Manager — see §3 note) | |
| logan.penrod | Logan Penrod — Designer | Design | Design Team | **Design Team** | — | |
| nathan.cox | Nathan Cox — Junior Designer | Design | Design Team | **Design Team** | — | |
| austin.healey | Austin Healey — Junior Technician | Production | *(none; direct)* | **Production Team** | — | |
| daniel.rosser | Daniel Rosser — Junior Technician | Production | Production Team | **Production Team** | — | |
| jesse.griffin | Jesse Griffin — Senior Technician | Production | Production Team | **Production Team** | — | |
| korben.jessop | Korben Jessop — Junior Technician | Production | Production Team | **Production Team** | — | |
| lian.silva | Lian Jentz Da Silva — Junior Technician | Production | Production Team | **Production Team** | **Remove stray Accounts User** | |

## 2b. Enabled non-employee / service / external accounts (6) — need a disposition

| User | Who / purpose | Current access | Recommended disposition | CEO ✔ |
|---|---|---|---|---|
| billing@sapphirefountains.com | Shared billing mailbox (not a person) | Accounts Manager + Purchase Master Manager + Purchase Manager/User | **✅ DONE — disabled 2026-07-24.** | |
| triton@sapphirefountains.com | Integration/AI service account | System Manager + Accounts Manager + full purchasing | **Scope to the minimal integration role** it actually needs; remove System Manager + Accounts Manager | |
| poseidon@sapphirefountains.com | Legacy service account | `Poseidon` profile | Review/retire alongside the Poseidon profile (WI-010 flagged; needs sign-off) | |
| john@skylinepathway.com | John Juntunen — external accountant/reviewer (WI-003) | **System Manager** profile | **Downgrade** to a scoped Accounts/Auditor profile — an external reviewer should not hold System Manager | |
| kendalyn.harris@sapphirefountains.com | Kendalyn Harris — no roles, no employee | none | Confirm business need or disable | |
| shellycekeyes@gmail.com | External personal Gmail — no roles, no employee | none | Confirm identity/need or disable (external email as System User) | |

## 2c. Disabled users (8) — already correct, documented for the record

angie.larsen (Left), laura.brimley (Left), josh.farris (Left), jacob.shefchik (Left), james@wasatchaquatics.com, lynn@wasatchaquatics.com, anup@d3vtech.com (vendor), sinjini@d3vtech.com (vendor) — all `enabled=0`. No action; do **not** re-enable without sign-off.

---

## 3. The Segregation-of-Duties decision the CEO must make

**Acceptance criterion:** *at least one user holds `Accounts Manager` who is NOT the daily `Accounts User` preparer.*

**Structural obstacle:** the `Accounts` and `Finance` Role Profiles both bundle **Accounts Manager _and_ Accounts User together**, so you cannot make Lisa a "preparer only" *via a profile* — a profile assignment gives her both. Three ways to satisfy preparer≠approver:

- **(a) Direct-role assignment (recommended, no new profile):** assign Lisa **Accounts User** directly (no profile for the accounts side) + Purchase Manager; assign **Accounts Manager** to the CEO (James) as the approver. Preparer (Lisa) ≠ approver (James). ✅ meets acceptance, no WI-010 change.
- **(b) New split profiles (WI-010 follow-up):** add `Accounts Preparer` (Accounts User) and `Accounts Approver` (Accounts Manager) profiles. Cleaner long-term, but reopens WI-010.
- **(c) Compensating control:** accept the dual-hold and enforce separation in the WI-044 approval *workflow* (a different user must action the approval transition). Weakest; documents the risk instead of removing it.

**Recommendation: (a).** Approver = **James Harris (CEO)**; daily preparer = **Lisa Symanski (Accounts User)**. Second approver (backup) — CEO to name one if desired (a 14-person org may want Nik as a break-glass approver, documented).

**`PO Approver` (WI-013 exemption holder):** currently James Harris + Nikolas Bradshaw. Confirm: CEO only, or CEO + sys-admin? Everyone else stays subject to the threshold.

**`Purchase Master Manager` (15 holders):** decide whether to trim it out of the Design Team profile (Phase-2 consolidation) so designers can't submit unlimited POs; out of WI-011's strict scope but recorded here because it interacts with WI-013.

---

## 4. Acceptance-criteria status

- [ ] `enabled=1 System User` with null/empty `role_profile_name` = 0 → **9 enabled users currently have no profile**; the target column above fills them.
- [x] Active employees with a linked `user_id` for the kiosk subset = **all 15 already set** (verified).
- [ ] ≥1 `Accounts Manager` who is not the daily preparer → satisfied by §3(a): James (approver) ≠ Lisa (preparer).

## 5. Rollback snapshot (before-state, for exact reversal)

Enabled users currently carrying a Role Profile: `daniel.blass, logan.penrod, nathan.cox = Design Team` · `lisa.symanski = Finance Team` · `daniel.rosser, jesse.griffin, korben.jessop, lian.silva = Production Team` · `richard.hansen = Sales Team` · `john@skylinepathway.com = System Manager` · `poseidon@ = Poseidon`. All other enabled users: `role_profile_name = NULL`. Capture the full `user, role_profile_name, roles[]` set immediately before applying (a one-line export) so any change is reversible.
