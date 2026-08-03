# WI-036 — Utah sales-tax guidance from the CPA, and what it settles

**Source:** John Juntunen, CPA (external), relayed 2026-08-03. This file is the audit-trail record of the guidance; OD-2 named the CPA's written confirmation as the go-live sign-off gate, so what he actually said is reproduced first and unedited.

## The guidance, verbatim

> - At a high level, a product or service is going to be subject to sales tax in Utah unless the legislation specifically exempts it.
> - Additionally, generally a sale of product is not subject to sales tax when it is sold to someone or a company who is not an end-user. For example, if Sapphire sold some piping to a customer who will, in turn, sell it to someone else, then that sale from Sapphire to the "middle-man" is not subject to sales tax.
>   - In this case, I recommend gathering a sales tax exemption certificate from that customer.
> - Additionally, a sale of a product or service is not subject to sales tax when the state legislature specifically exempts an organization type or type of customer from sales tax. For example, many sales involving a religious organization do not require sales tax.
>   - In this case, I recommend gathering a sales tax exemption certificate from that customer.
> - Lastly, a sale may not be subject to sales tax if the contract with that customer does not allow Sapphire to charge sales tax.
>   - This would be rare.
>
> My overall recommendation with this concept is to include the option to make an invoice/sale subject to sales tax by clicking a checkbox. If you can mimic QuickBooks in this way, that would be great.

## What this settles

**The default flips to taxable.** Every sale charges tax unless something specific removes it. That is simpler than the design assumed and it is a real decision — configuration should make *taxable* the path of least resistance and exemption the deliberate act.

**Exemption is a property of the customer, not of the work.** Three of his four cases — resale, exempt organization, contractual — attach to *who is buying*, and all three are evidenced by a certificate on file. That maps cleanly onto ERPNext's native **Tax Category on Customer** driving a **Tax Rule**, which is a set-once-per-customer arrangement rather than a per-invoice judgement. The fourth case (legislative exemption of a product or service) attaches to *what is sold* and is the item axis.

**A per-document override is wanted.** He explicitly asked for the QuickBooks-style checkbox, so the design needs both layers: a durable customer default, and a per-invoice way to depart from it.

## ⚠️ What this does **not** settle — and it is the big one

**The guidance does not mention real-property improvement, which is the premise the entire chart of accounts was designed around.**

OD-2 records the position as: *"Utah taxes improvement to real property (contractor pays tax on materials; customer not charged) differently from repair of tangible personal property (taxable to customer)."* From that, the design derived:

- **Build treated as real-property improvement** — Sapphire is the *consumer* of the materials, pays tax on purchase, and does **not** charge the customer sales tax on the improvement (`COA_DESIGN.md` §6).
- **`2136 Use Tax Payable`** and **`60920 Sales & Use Tax Expense`** — the self-assessment entry for Build materials bought untaxed, which is common with out-of-state fountain-equipment vendors.
- Stream-differentiated tax templates in WI-036, and the taxable-vs-exempt-by-stream columns in WI-038's filing procedure.

**Read literally, the guidance points the other way.** A Build customer is an end-user, is usually not an exempt organization, and usually has no contractual bar — so under the framework as written, **Build jobs would charge the customer sales tax.** That is the opposite treatment, and it changes the tax position, the templates, the filing, and whether `2136` and `60920` are load-bearing accounts or dead ones.

Two readings are possible and they are not reconcilable by us:

1. He gave the **general framework** and has not yet applied it to Sapphire's Build work specifically.
2. He is saying **there is no special Build treatment** — charge tax unless one of the four exemptions applies.

**This is a tax position with money attached, and the plan is explicit that neither it nor anyone executing it is the tax authority.** It must be asked and answered before WI-036 configures anything.

### Also still open

| Question | Why it blocks |
|---|---|
| **Rates and jurisdictions** — which rates apply where | WI-036 cannot build templates without them. Production has three rate buckets (4%, 6%, 6.25%) and 40 imported jurisdiction accounts with three competing naming schemes. |
| **Per-rate or per-jurisdiction tax leaves** | Structural to the chart. Rate buckets `2131–2135` only work if each maps to exactly one TC-62M location code. **Must be answered before WI-029 imports the chart (26–31 Dec).** |
| **What is the account QuickBooks calls `ST 4%`?** | One of only three real rate buckets; we cannot tell which jurisdiction it represents. |
| **Use-tax accrual timing** — per purchase or period-end | Only matters if the real-property treatment stands. Falls away entirely under reading 2. |
| **Is the Virginia registration lapsed?** | Two Virginia tax accounts are marked for retirement on that assumption. |

## The exemption mechanism — proposed design

Native-first, and it implements his framework rather than approximating it.

### Layer 1 — the customer default (native, no code)

**`Tax Category` on Customer**, driving a **Tax Rule** that selects the sales-tax template:

| Tax Category | Effect | Evidence required |
|---|---|---|
| *(blank)* | Standard Utah sales tax | — |
| `Resale` | No tax charged | Resale exemption certificate on file |
| `Exempt Organization` | No tax charged | Exemption certificate on file |
| `Contractual Exemption` | No tax charged | The contract clause |
| `Out of State` | No Utah tax | Ship-to evidence |

This is the right home for his advice, because an exemption certificate describes a *customer*, not an invoice. Set once, applies to everything that customer buys, and survives staff turnover.

### Layer 2 — the per-document checkbox (small custom field)

A **`Exempt from Sales Tax`** check on Quotation / Sales Order / Sales Invoice, plus a **required reason** when it is ticked — Resale · Exempt Organization · Contractual · Legislative Exemption · Other.

This is what he asked for, with one deliberate addition: **QuickBooks' checkbox records only *that* tax was not charged; this records *why*.** That is the question an auditor asks, and answering it from a report rather than from memory is the difference between a five-minute answer and an afternoon. It also makes exemptions countable — a monthly review of exempt sales by reason is a real control.

### Layer 3 — the certificates themselves ⚠️ **currently nowhere**

He recommends gathering an exemption certificate twice, and **nothing in the migration plan stores one.** A claimed exemption with no certificate on file is an undefended position in an audit, and the state's assessment lands on Sapphire, not the customer.

Proposed: on Customer, an attachment plus `Exemption Certificate Expiry`, and a check that flags a customer claiming exemption with no certificate or an expired one. Certificates do expire, and nothing today would notice.

**This is a genuine gap in the work item, not an implementation detail.** WI-036 as written covers rates and templates and says nothing about evidence.

## Status

- [x] CPA guidance received and recorded (this file).
- [ ] **Real-property / Build treatment confirmed** — blocks WI-036 and the WI-029 chart freeze.
- [ ] Rates and jurisdictions supplied.
- [ ] Per-rate vs per-jurisdiction leaves resolved — **before 26 Dec**.
- [ ] Exemption-certificate handling agreed and added to scope.
- [ ] Tax Categories, Tax Rules and templates built (WI-036 / WI-037).

Nothing is configured yet, deliberately. Production currently has **0 Tax Rules and 0 Tax Categories**, so there is no automated tax determination at all — which is the state to fix, but not before the Build question is answered.
