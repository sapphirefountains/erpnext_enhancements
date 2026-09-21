"""Our own address and phone, as printed at the top of every outgoing document.

One copy, read by `print_style.letterhead()` for every format on the print design system
(the Purchase Order and the three customer-facing sales formats today). It lives in its
own module rather than in `print_style` because it is a fact about the company, not about
the design — the line to edit when the office moves is here, and nothing else changes.

**The letter head cannot carry this.** `Sapphire Fountains Default` is a right-aligned logo
and nothing else: no address, no phone, empty footer. Verified on production. So every
custom-format template has to draw the contact details itself, which the shared
letterhead now does (and the site letter head is no longer rendered beside it — two logos
on one page is worse than one).

The address and the phone are here for opposite reasons, and it matters which is which.

The **address is a fallback, not the source.** Each document already carries the company
address it was raised against, and the letterhead prefers it — so a document naming a
different company address prints what it actually says rather than a constant contradicting
it. On production that field is populated on 657 of 657 Quotations, 1000 of 1000 Sales
Invoices, and 147 of the 157 Purchase Orders. The constant is for the remainder: a
customer-facing document with a blank address block is precisely the silent defect these
formats exist to avoid.

Note the field is **not the same on both sides**. Sales documents call it
`company_address_display`; Purchase Order calls it `billing_address_display` and has no
`company_address` at all. That is why `print_style.letterhead()` takes the fieldname rather
than hard-coding one — getting it wrong prints nothing and raises nothing, since Jinja
renders a missing attribute as empty.

The **phone is not a fallback, because there is nothing to fall back from**: the number is
stored nowhere in ERPNext. `Company.phone_no` is null, and so is `Address.phone` on
`Sapphire Fountain-Billing`, the one company address record on the site. Fill either in and
this can become data-driven like the address; until then `COMPANY_PHONE` is the only copy of
the number anywhere, and the line to edit when it changes.
"""

COMPANY_ADDRESS_HTML = "85 W 300 S<br>Bountiful, UT 84010"
COMPANY_PHONE = "+1 (801)-837-2199"
