# Supplier Returns

How something we bought goes back to the supplier, and how we know the money came back.
Shipped in v1.807.0 (Nik, 2026-11-12: "a return process that is simple and tracked").

Code: [`inventory_enhancements/supplier_return_rules.py`](../erpnext_enhancements/inventory_enhancements/supplier_return_rules.py)
(every judgement, no Frappe),
[`inventory_enhancements/supplier_returns.py`](../erpnext_enhancements/inventory_enhancements/supplier_returns.py)
(the reads, the steps, the standard documents, the email) and
[`api/supplier_returns.py`](../erpnext_enhancements/api/supplier_returns.py) (the endpoints).

## What ERPNext already does, and what it lacks

Read in ERPNext **v16.54.0**, the version production ran on 2026-11-12.

| Need | ERPNext's own piece | What it does |
|---|---|---|
| Take the goods out of stock | **Purchase Return**: a Purchase Receipt with `is_return`, against the original receipt (`make_purchase_return`) | For a stock item: a stock ledger entry out, and the receipt's ledger entry reversed (credit the stock account, debit Stock Received But Not Billed). It also lowers the received quantity on the purchase order line. |
| Lower what we owe | **Debit Note**: a Purchase Invoice with `is_return`, against the bill (`make_debit_note`) | Debits the supplier's payable account. Set against the bill when the bill is still unpaid; otherwise it carries the amount the supplier owes us. |
| Take the refund | **Payment Entry**, type Receive, from the Supplier (`get_payment_entry` on an invoice whose outstanding amount is negative) | Debits the bank, credits the payable account. |
| Use the credit on a later bill | **Payment Reconciliation** | Sets the debit note against another bill. |

Nothing in ERPNext records the **request**: what we asked the supplier, the return number they
gave, the tracking number, and whether the money ever came back. There is no RMA document for
purchases. That is the gap, and the only thing this app adds.

## The design in one paragraph

A **Supplier Return** is a tracking record. It never moves stock and never posts to the ledger.
Each step is a button; the status is never typed. At "Sent Back" one click drafts ERPNext's own
Purchase Return, cut down to this return's lines, for the person to check and submit. When a bill
exists in ERPNext, one click drafts the Debit Note; the refund is ERPNext's own Payment Entry.
The Supplier Return links those documents and reads its outcome from them. While bills are still
entered in QuickBooks, the outcome is a date, an amount and a reference typed by the person who
saw the money, and the record says plainly that it is not backed by an ERPNext payment.

## The steps

| Status | How it gets there | Who |
|---|---|---|
| **Draft** | Create > Return to Supplier on a Purchase Receipt or Purchase Order, the Return buttons in the Procurement Tracker, or Return to supplier on an item in Stock Scan. The lines come from the receipt at the most that can still go back; the person lowers quantities, picks a reason per line and adds photos as attachments. | Purchase User, Purchase Manager, Stock User, Stock Manager |
| **Requested** | **Send Return Request** emails the supplier (the person picks the recipient and can edit the message; the PDF is attached; the email is on the timeline). Or **Requested Another Way** for a phone call, the counter or the supplier's website. | same |
| **Approved by Supplier** / **Denied** | **Supplier Approved** records the return number (optional: some suppliers give none). **Supplier Denied** needs what they said. | same |
| **Sent Back** | **Mark Sent Back**: by carrier (carrier and tracking number required), dropped off, or supplier pickup. This is when stock leaves, so the form then asks for the Purchase Return. A return dropped at a counter can go from Draft straight here. | same |
| **Refund Received** / **Credit Received** | From the linked Payment Entry or Debit Note, on its own. Or **Record Money Received**, typed, while no such document is linked. | Accounts User, Accounts Manager, Purchase Manager |
| **Replacement Received** | **Record Replacement Received** with the new Purchase Receipt (received against the same purchase order line: the Purchase Return lowered that line's received quantity, so ERPNext lets it be received again). | Purchase and stock roles |
| **Closed Without Credit** | The items went back and nothing is coming. Needs a reason. | Accounts roles, Purchase Manager |
| **Canceled** | Before the items have gone. Needs a reason. | Purchase and stock roles |

**Reopen** (Purchase Manager, Accounts Manager) undoes a step recorded by mistake. An outcome that
came from a document is undone by canceling that document.

The form's headline says who the return is waiting on and for how many days. The list shows days
at the current step, has a **Waiting on Money** button, and its sidebar groups by status. The
workspace shortcut counts the returns waiting on money.

## Quantities

A line may not return more than was received on its receipt line, less:

- what other Supplier Returns hold for that line (any that are not Canceled or Denied), and
- what was returned with ERPNext's own Purchase Return button and is linked to no Supplier Return.

A second return against the same receipt is a second Supplier Return. ERPNext checks again when
the Purchase Return is saved (`validate_returned_items`), so two people racing each other are
stopped there.

Once a return leaves Draft its items and quantities are locked: the supplier was asked about
those. Fees, tax, tracking and notes stay editable.

## Stock and accounting, case by case

Perpetual inventory is on. Stock Received But Not Billed is account 2210.

| Case | Stock document | What posts | Money |
|---|---|---|---|
| **A stock item, received into stock, not billed** (the normal case once Maintain Stock is on) | Purchase Return for the returned quantity | Stock out of the warehouse. Credit the stock account, debit 2210, at the receipt's rate. | No bill, so no Debit Note. The bill will come in lower (see below). A refund or credit is typed until bills are in ERPNext. |
| **A non-stock line** | Purchase Return, quantity only | **Nothing** (production has provisional accounting for non-stock items off). It lowers the received quantity on the order line and on the receipt. | Same as above. |
| **An old receipt line of an item later switched to a stock item** | **None. Record only.** | Nothing. | Tracked on the Supplier Return like any other. |
| **Partial return** | Purchase Return for part of the line | As the first row, for that part. | |
| **Second return, same receipt** | A second Purchase Return | ERPNext subtracts the first. | |
| **Replacement** | Purchase Return, then a new Purchase Receipt against the same order line | The return reverses; the new receipt posts as any receipt does. | No money moves. |
| **Returned after the bill, before it was paid** | Purchase Return | As the first row. | Debit Note against the bill: debit the payable account, credit 2210. It is set against the bill, so we pay less. Status: Credit Received. |
| **Returned after the bill was paid** | Purchase Return | As the first row. | Debit Note; the supplier now owes us. Payment Entry (Receive) against it when the refund arrives: Refund Received. Or Payment Reconciliation against a later bill: Credit Received. |

### Why the record-only case exists

When an item's Maintain Stock is turned on after it was received, its old receipt line is a
stock item's line that never wrote a stock ledger entry. ERPNext builds a return's ledger from
what keeps stock **today**, so a Purchase Return would take out stock the line never put in.
`maintain_stock.refuse_unstocked_receipt_return` refuses that, and it is right to.

The Supplier Return does not fight the guard. It leaves such a line off the Purchase Return (it
never reaches ERPNext), records the return anyway, and says on the record why no stock document
was made. If the item has been counted into stock since, the count is where it comes out: the
Count Desk. The guard's `ignore_maintain_stock_guard` flag is never set by this code, and a test
fails the build if it ever is.

### The bill that should come in lower

A return before the bill arrives means the supplier should bill for less. The person entering the
bill finds out three ways:

1. **Billing from the receipt in ERPNext** (Purchase Receipt > Create > Purchase Invoice) already
   subtracts what a submitted Purchase Return took back. Nothing to remember.
2. **A bill typed by hand or made from the order** shows an orange message when it covers a
   receipt line or an order line on a standing Supplier Return with no Debit Note
   (`supplier_returns.tell_the_bill`). It never refuses the bill.
3. **While bills are entered in QuickBooks**, the report *Supplier Returns: Money Outstanding*
   says "Not billed in ERPNext. The bill should come in lower" on each such return. Check a
   supplier there before entering their bill.

## Knowing whether the money came back

`money_source` on the record is one of:

- **Typed by hand.** A person entered the amount, the date and a reference. The form shows a
  yellow line, the timeline comment and the report column say it in full: "Typed by hand. It is
  not backed by an ERPNext payment."
- **Debit Note** or **Payment Entry.** The amount, date and reference are read from the document
  each time the return syncs. They cannot be typed over: the buttons are not offered, the
  endpoint refuses, and a form save that tries is put back.

A Debit Note the supplier still owes us on is **not** "received": the return stays Sent Back and
says how much is owed. A daily job re-reads the Debit Note of each return still waiting, because
Payment Reconciliation changes a note's outstanding amount without any event this app hears.

**Report: Supplier Returns: Money Outstanding.** By supplier, oldest first: sent back on, days
waiting, expected, received, difference, where the amount comes from, and where the bill stands.
Views: Waiting on money (default), Came back short or over, All returns.

## The email

- The wording lives in the Desk, in the Email Template **Supplier Return Request** (seeded once;
  after that it is the buyer's). If it is missing, the built-in wording is used and the dialog
  says so.
- The dialog shows the recipient (the address on the return, then the supplier's contacts, then
  the supplier's own address, or another address typed in), the subject and the message, all
  editable, with a preview in the letterhead.
- It goes out through the app's one email layout (`email_style.wrap`) with the PDF attached
  (print format **Supplier Return - Sapphire**), and optionally the photos attached to the return.
- **It is sent only by the Send button.** Saving, any other step, the documents' hooks and the
  daily job never send. The endpoint also refuses an API key, a token and a background job.

## Effects elsewhere

- **Purchase Order's Order Stage** (`po_order_stage.advance_on_receipt`) already walks an order
  back when a Purchase Return lowers what was received: Received becomes Partially Fulfilled or
  Awaiting Confirmation, with a timeline comment. Nothing was changed there. A return that ends in
  a refund leaves the order saying part of it is not here, which is true; close the order line if
  it will not be re-ordered.
- **Reports that skip returns** (`api/supplier_search.py`, `kpi_dashboards/snapshots.py`, Store
  Run Charge Matching) filter on `is_return = 0` and are untouched.
- **For other modules:** `supplier_returns.returned_lines(purchase_order_items=None, project=None)`
  gives one dict per Supplier Return line (`supplier_return_rules.RETURNED_LINE_KEYS`). It never
  raises and gives `[]` before the DocType is installed.
- **No Custom Field** is added to Purchase Receipt, Purchase Invoice or Payment Entry. The links
  live on the Supplier Return; the standard forms ask which return they belong to and say so in a
  line at the top.

## What only a real bench can prove

The bench-free suites (`tests/test_supplier_return_rules.py`, `tests/test_supplier_returns.py`)
prove the rules, the quantity limit on real queries, which document each case chooses and what
that document is handed, the money rule, and that nothing but the Send button sends. ERPNext's
mappers are stand-ins there. A bench is needed for:

- the standard **Purchase Return actually posting**: stock out, and credit stock / debit Stock
  Received But Not Billed, at the receipt's rate;
- a **non-stock** Purchase Return posting nothing and lowering the order's received quantity;
- the **Debit Note** posting, and whether ERPNext sets it against the bill or leaves the amount
  owed on the note;
- `get_payment_entry` building a **Receive** payment from a debit note, and its submit setting
  Refund Received;
- the **Maintain Stock guard** still refusing a hand-made return of an old line;
- the unsaved Supplier Return opening from `frappe.model.open_mapped_doc`;
- the email, the PDF and the print format as they really render;
- the workspace shortcut's count, the list's button and the report on real rows.

## Rehearsal on the production mirror

Run as a Purchase Manager who also holds Accounts User. Use a supplier whose email is your own.

1. **Deploy and migrate.** Expect in the migrate output: `seed_supplier_return_request_template:
   created 'Supplier Return Request'` and `supplier_returns_hub_links: workspace
   inventory_enhancements imported`. Check `Supplier Return` and `Supplier Return Item` exist
   (`select name, module from tabDocType where name like 'Supplier Return%'`) and that no
   `Deleted Document` row names them.
2. **Stock item, not billed.** Pick a submitted Purchase Receipt with a stock item that has stock.
   Note the item's quantity in its warehouse and the balance of account 2210. Purchase Receipt >
   Create > Return to Supplier. Lower the quantity to part of the line, pick a reason, save.
3. **Email.** Send Return Request to your own address. Check the email arrives in the letterhead
   with the PDF, the status is Requested, and the email is on the timeline.
4. **Approve and send back.** Supplier Approved with a return number. Mark Sent Back by carrier.
   Accept the offer to create the Purchase Return. On the draft: only this return's line, a
   negative quantity, the right warehouse. Submit it.
5. **Check the posting.** Stock Ledger: the quantity out. General Ledger for the Purchase Return:
   a credit to the stock account and a debit to 2210 for quantity x receipt rate. The Supplier
   Return's Stock line reads "Purchase Return ... took the items out of stock." The purchase
   order's % Received dropped and its Order Stage moved back with a comment.
6. **Typed money.** Record Money Received. Check the yellow "not backed by an ERPNext payment"
   line, and the row in the report under "All returns".
7. **Non-stock line.** Repeat 2, 4 and 5 for a non-stock line. The Purchase Return submits and has
   **no** General Ledger and no Stock Ledger entries; the order line's received quantity dropped.
8. **Record only.** Find a receipt line of an item switched to a stock item after it was received
   (`maintain_stock.unstocked_receipts`), or switch a test item with Turn on Maintain Stock. Start
   a return for that line and mark it Sent Back: no Purchase Return is offered and the record says
   why. Then confirm the guard still stands: Purchase Receipt > Create > Purchase Return on that
   receipt must be refused at save.
9. **Second return.** Start another return for the rest of the line from step 2. The quantity
   offered is what the first left. Try one more: nothing is left.
10. **Bill, then return.** Make and submit a Purchase Invoice from another receipt. Start a return
    for one of its lines, mark it Sent Back, submit its Purchase Return. Create Debit Note: one
    line, `Update Stock` off. Submit. The bill's outstanding amount dropped and the return reads
    Credit Received with the note's amount; the amount cannot be typed.
11. **Paid bill, then refund.** Pay a bill in full, return a line, submit the Debit Note (it now
    carries a negative outstanding amount and the return still reads Sent Back). Create Refund
    Payment, fill the bank account, submit. The return reads Refund Received with the payment's
    amount. Cancel the payment: it reads Sent Back again.
12. **Replacement.** A return asking for a Replacement: after its Purchase Return, Purchase Order >
    Create > Purchase Receipt offers the returned quantity again. Submit it, then Record
    Replacement Received with that receipt.
13. **A bill over a return.** With a return Sent Back and no Debit Note, start a Purchase Invoice
    from that receipt: the returned quantity is already off the line. Type one by hand against the
    same order line: the orange message names the return.
14. **Stock Scan.** On a phone, open the item, Return to supplier, pick the receipt, enter a
    quantity and reason. A Draft appears in the list. Back closes each sheet.
15. **Back and Forward** through the form, the list with Waiting on Money, and the report.

## Open questions for Nik

1. **Approval above an amount.** Today nobody approves a return. Recommendation: none for now. A
   return gives money back, and the Purchase Order threshold already gates the spending. Revisit
   if restocking fees become large.
2. **Where restocking fees and return shipping post.** Today they are figures on the record only.
   Recommendation: once bills are entered in ERPNext, add one expense account for restocking fees
   and book return shipping to the freight account the bills already use, as lines on the Debit
   Note or the carrier's bill. Not before: with bills in QuickBooks the entry would have no other
   side here.
3. **Cost on the job when a returned item was already charged to a project.** A Purchase Return of
   a stock item still on the shelf reverses nothing on the job (the job is charged when stock is
   issued to it). If the item was already issued or consumed on the job, it has to come back to
   the shelf first (a stock entry carrying the project), and then be returned. Recommendation:
   keep that as two visible steps and have the Project Materials report show "returned to
   supplier" from `returned_lines`. For non-stock lines the job's cost comes from the bill, so the
   lower bill or the Debit Note (which carries the project) corrects it.
4. **Who may mark money received.** Built as asked: Accounts User, Accounts Manager, Purchase
   Manager. On production 18 people hold Accounts User and 16 hold Purchase Manager, so this
   gates almost nobody. Recommendation: move it to the Finance Team role (10 holders) in a small
   follow-up once Nik confirms.
5. **A return with no receipt.** A return needs a submitted Purchase Receipt. 104 submitted orders
   on production are Closed, many never receipted. Recommendation: receive first, then return, so
   stock and the order stay true; do not add a receipt-less return.
6. **Sales tax.** "Tax to Be Refunded" starts as the receipt's tax in proportion (77 of 188
   receipts carry tax) and can be changed. The Purchase Return and Debit Note use ERPNext's own
   tax rows, which the person checks on the draft.
