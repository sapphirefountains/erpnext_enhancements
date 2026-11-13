# `carrier_tracking/` — FedEx and UPS package status

Put a package's tracking number on the purchase order it belongs to, and ERPNext asks FedEx or
UPS where it is and when it should arrive, so nobody has to go to the carrier's website
(ER-2026-1333277, from the warehouse lead, 2026-11-13).

**Off means off.** Until a System Manager turns on *Ask Carriers for Package Status* in **Carrier
Tracking Settings**, the Purchase Order form shows nothing from this module and the endpoints refuse.
With the switch on, numbers can be recorded and shown (an *Other* carrier needs no credentials); a
carrier is asked only once it also has its own switch and both credentials. See *Turning it on*.

## Why a module of its own

`package_dispatch/` is the **outbound** Package Dispatch form (sending a package out), with its own
role and switch; inbound tracking on a purchase order is a different job for different people.
`inventory_enhancements/` is already the largest module. The carrier client is meant to be shared:
Package Dispatch and Supplier Return (`inventory_enhancements/doctype/supplier_return`, which already
has `carrier` and `tracking_number` fields) can call `config.track(carrier, number)` later without
one of those modules importing the other. Wiring them up is not part of this release.

## Files

| Path | Purpose |
|---|---|
| `carriers.py` | Rules with no I/O: which carrier a number belongs to (by format; an explicit carrier always wins; "Unknown" is an answer), the carriers' public tracking pages, our six status codes and their labels, and `mask`, which takes secrets out of any text before it is stored or logged |
| `normalize.py` | One FedEx `trackResults[]` entry or one UPS `package[]` entry into our shape: status, the carrier's words, estimated delivery, delivered at, last location. Times stay in the carrier's local time. The status tables cite the carriers' code lists |
| `client.py` | The HTTP half, `requests` only: the OAuth client-credentials token (cached in Redis for five minutes less than its life, never in the database, never logged), the Track call, one token refresh and one retry on a 401, back-off on a 429, and an outcome for every failure. **Never raises.** Every URL and header cites the carrier page it was read from |
| `config.py` | Carrier Tracking Settings read for the client (`carrier_config` is `None` unless the master switch, the carrier's switch and both credentials are there), Redis as the token cache, and `track(carrier, number)` |
| `package_rules.py` | Adding a number: cleaning it, the carrier choice (Auto, FedEx, UPS, Other) and whether it was a guess, the tracking page, every carrier field at its starting value, and for Other or an unknown carrier the "recorded, not checked" state. `order_accepts_packages` (which orders take a number), `MANAGE_ROLES` (the permission rule below). No Frappe |
| `poll_rules.py` | When a package is checked and what an answer does to it: due rows and their order, the limits (constants, each explained), the two kinds of failure, and every reason polling stops. No Frappe |
| `tracking.py` | The hourly `poll` (bounded, silent while off, one bad row cannot stop it, never raises), `check_rows` for calls inside a web request (a few packages and seconds per request, a per-person window), `claim_order_cooldown` (one round per order per minute, shared by Add and Refresh), and the single writer of a row's carrier fields (`db.set_value`, no `modified` bump) |
| `permissions.py` | The `has_permission` and `permission_query_conditions` hooks: a package is visible only with read on its order, using Frappe's own match conditions for Purchase Order. Fails closed |
| `purchase_order_hooks.py` | Purchase Order `on_trash` (a package never blocks deleting an order; a draft amendment hands its packages back) and `after_insert` (an amendment takes the canceled order's packages). Never raise |
| `doctype/tracked_package/` | **Tracked Package**: one tracking number on a Purchase Order (`reference_doctype` / `reference_name`), the carrier chosen and used, the tracking page, and what the carrier last said: status, its words, estimated delivery, delivered on, last location, last checked, last answer, last problem, and whether and why it is no longer checked |
| `doctype/carrier_tracking_settings/` | **Carrier Tracking Settings**, a Single: the master switch, and per carrier a switch, Sandbox or Production, a client id and a client secret (Password). System Manager only. Nothing in the app writes to it |

## A record of its own, not a child table of Purchase Order

The request suggested a shipment child table on the order. Tracked Package is a DocType of its own
with a Dynamic Link to the order instead, because:

* **Adding a number must not edit the order.** Adding a child row to a submitted order needs the
  field set allow-on-submit *and* the person to hold submit on Purchase Order. On this site only PO
  Creator does; purchasing (Purchase User, Purchase Manager) and the warehouse (Stock User, Stock
  Manager) read orders and cannot change them, and they are the people who know the tracking number.
* **An open order form would erase it.** Frappe deletes, on save, every child row the form did not
  load. A number added from the banner, or a status written by the poller, while someone has the
  order open would be wiped by their next save.
* **It can be calendared later.** Frappe's calendar, list and report views need a DocType of their
  own; a child table has none of them.

### Who may add or remove a tracking number

*create* (to add) or *delete* (to remove) on Tracked Package **and** *read* on the order. The
DocType grants create, delete and read to PO Creator, Purchase User, Purchase Manager, Stock User,
Stock Manager and System Manager (`package_rules.MANAGE_ROLES`, held equal to the JSON by a test).
Nobody but System Manager holds *write*, so nobody can type a status. Neither right touches the
order: no write, no submit, no allow-on-submit field, so nobody's rights over the order itself are
widened. External Contractor reads orders and sees no tracking.

On top of the roles, a package is visible only with its order (`permissions.py`): one package needs
read on its Purchase Order, and lists and reports of Tracked Package are cut to orders the person can
read, using Frappe's own match conditions for Purchase Order (User Permissions, owner rules, shares).
Someone who reaches Purchase Orders only through shares (no role read on Purchase Order, as Stock
Manager here) lists only the packages of the orders shared with them, or none: Frappe's match
conditions return nothing for that case, so `permissions.query_conditions` builds it from
`frappe.share.get_shared`.
A REST insert goes through the same gate in `TrackedPackage.validate`: the switch must be on, the
order readable and still able to take a package, and every carrier-written field is reset to its
starting value, so a create cannot arrive "Delivered" or with a `last_checked` that skips polling.

### Which orders take a number, and what happens to them later

*Add tracking number* is offered (and the endpoint accepts it) on a draft or submitted order that
is not Closed, Completed, Canceled, Delivered (drop-ship) or fully received
(`package_rules.order_accepts_packages`). An order with packages always shows them.

* **Deleting an order** removes its packages first (Purchase Order `on_trash`), so a package never
  blocks the delete. Frappe runs `on_trash` before its Dynamic Link check (v16.56.0
  `delete_doc.py`, lines 177 and 184-185), which is what makes this possible.
* **Amending a canceled order** moves its packages to the amendment as soon as the amendment exists
  (`after_insert`): they are the same boxes. Only if the person amending can read the canceled order,
  since a REST insert can name any canceled order in `amended_from`. Running twice moves nothing; a number already on the
  amendment is not doubled. Deleting that draft amendment gives them back.
* **A canceled order that is never amended** keeps its packages, shown read-only (no Add, Remove or
  Refresh) and no longer polled.

### A carrier guessed from the number

Only `1Z…` (UPS) and a 22-digit number starting `96` (FedEx Ground) are certain. A bare 12- or
15-digit number is *probably* FedEx, so with carrier Auto it is tracked as FedEx **marked as a
guess**: the banner says "FedEx (guessed)" and the poller gives up on it after two days of "not
found" instead of a week, with a note to add it again with the carrier picked. A guess FedEx does
answer for may still be someone else's shipment, so the row stays marked "(guessed)", shows where
the carrier says it is going ("Going to Orem, UT, US", when the answer has it), and says in one
sentence that the carrier was guessed and how to correct it. Asking the person to
pick a carrier for every 12-digit FedEx Express number, which is most of what arrives, would be the
worse trade.
### The purchaser's date is never overwritten

Purchase Order already has *Expected Delivery Date* (`custom_expected_delivery_date`), the
purchaser's own date. The carrier's estimate lives on the Tracked Package and is shown beside it;
nothing here writes to the order.

### Polling

Hourly (`tracking.poll`), and only while the feature and at least one carrier are on with
credentials; otherwise it does nothing and logs nothing. A run checks at most 25 packages in at most
120 seconds, each at most every 2 hours, never-checked first, then oldest-checked first, on
submitted orders that are not Closed, Completed, Canceled, Delivered (drop-ship) or fully received.
It stops polling a package that is delivered, Other or of unknown carrier, invalid, "not found" for
a week without ever being found (two days for a guessed carrier), unusable 12 times in a row after
the carrier had answered once, unchanged for 21 days, or tracked for 90 days. Until the carrier has
answered once, "not found" is governed by the week alone and does not count towards the 12.

A failure about the carrier never counts against a package and never stops one (except at 90
days): a refused login, "too many requests", an outage, **any failure at the token endpoint**, and
**any error the carrier gives that is not one of its documented package-level codes** (an account
lock, "log in again", a malformed-request code, a wrong path). That carrier is skipped for the rest
of the run, and a failed token request is remembered for two minutes so the next package does not
ask again. Only these mean something about the package: FedEx `TRACKING.TRACKINGNUMBER.NOTFOUND`,
`TRACKING.DATA.NOTFOUND` (also as an `output.alerts` entry) and `TRACKING.TRACKINGNUMBER.INVALID`,
matched exactly, and UPS's 404 in its documented error envelope. A UPS 400 is documented only as
"Invalid request", so it is never read as an invalid number.

**A refusal that may be about one number** (a 400 or 404 with a code we cannot place, scope
`number`) is told apart from one about every number by comparing packages in the same run
(`tracking.Round`): if a second package of the same carrier gets it too, it is the carrier's and
counts for neither (the carrier is skipped for the rest of the run); if another package gets a real
answer, it was that number's. A package that only ever gets this refusal, with no answer ever, is
stopped after 3 days with "Check that the number and the carrier are right", the typical case being
a mistyped number on a carrier picked by hand. One package's refusal no longer costs the carrier the
rest of the run. The ambiguous case left is a carrier with a single package due in the run: that
refusal is taken as about the number, and a person's Refresh restarts the package if the carrier
then answers. A 200 whose body is not JSON is the carrier being unavailable, never a package
failure.

"Could not check" never changes a stored status. Each answer is committed on its own, and a row
whose check or save fails is rolled back, stamped as checked (so it goes to the back of the line)
and logged once per run, so one bad row cannot stop the poller. A deploy that flushes the queue
costs one run. The reasoning for each number is in `poll_rules.py`.

A person's Refresh, and the first check after Add, run inside the web request, so they are capped:
at most 5 packages and 25 seconds per request (the rest are the poller's), one round per order per
minute shared by Add and Refresh (adding and removing a number cannot get round it), and 30 carrier
calls per person per 10 minutes, counted with an atomic Redis increment. A Refresh refused by that
limit says so and says how many minutes to wait.

## On the Purchase Order

`public/js/po_package_tracking.js` draws nothing while the master switch is off. With it on, an
order that has packages shows a compact banner at the top: each tracking number with the carrier's
status in plain words, the carrier's estimated arrival beside the purchaser's own *Expected Delivery
Date*, when it was last checked, a link to the carrier's tracking page (a new tab), and *Remove* for
those who may. A canceled order shows its packages read-only. An order with none shows only a small
*Add tracking number* button, and only while it can still have a package on its way. The button opens
a small dialog (number, carrier Auto/FedEx/UPS/Other, and a link for Other). *Refresh* asks the
carriers now. When no carrier is live, the banner says so and the links still work. Nothing on the
banner writes to the order, so the form never turns "Not Saved", and nothing changes the route, so
Back and Forward behave.

**Integrations Health** has a tile for each carrier: not configured, switched off, Sandbox (amber:
test data) or Production, the carrier's last answer and its age, and the last problem (masked).

## Later: what this data model leaves open (checked, not built)

* **Arrivals calendar** (the warehouse lead's second ask). Tracked Package is a DocType of its own,
  so a Frappe calendar view needs only a `tracked_package_calendar.js` mapping `start` and `end` to
  `estimated_delivery` (falling back to `delivered_on`) and the title to the number and supplier.
  `supplier` is copied onto each row for exactly this; filtering by project would read the order's
  `project` through `reference_name`. Rows without an estimate (Other, not checked yet) would need
  the purchaser's *Expected Delivery Date* from the order as a fallback, which a calendar endpoint
  can join. Nothing in the model has to change.
* **Procurement Tracker** (the Project form). Worth showing: it already lists each order's lines and
  their stage. `api/package_tracking.get_packages` is per order; the tracker would want one query
  for all the project's orders (`reference_name in (...)`, the newest row per order) and a status
  chip beside the order. Small, but it touches the tracker's bundle and its level guard
  (`REQUIRED_LOGIC_LEVEL`), so it is its own change.
* **Receive Items** (`public/js/po_receive_items.js`). Worth a line at the top of the dialog
  ("UPS says delivered today at 2:14 PM") and nothing more: the receiver is looking at the boxes, and
  the carrier's word must never pre-fill a received quantity.
* **Package Dispatch and Supplier Return** can call `config.track(carrier, number)` for their own
  outbound numbers; Tracked Package's `reference_doctype` would then allow them.

## What was checked in the carriers' documentation (2026-11-13)

Nobody has credentials yet, so **no request has been made to either carrier**. Every endpoint,
header and field was read from the carriers' current public documentation, cited in `client.py`'s
docstring, and the tests use invented payloads shaped like the documented ones.

| | FedEx | UPS |
|---|---|---|
| Hosts | `apis-sandbox.fedex.com`, `apis.fedex.com` | `wwwcie.ups.com` (CIE), `onlinetools.ups.com` |
| Token | `POST /oauth/token`, form `grant_type`, `client_id`, `client_secret` | `POST /security/v1/oauth/token`, HTTP Basic id:secret, form `grant_type=client_credentials` |
| Token life | `expires_in` 3600 ("one hour") | `expires_in`, sent as a string; no figure published |
| Track | `POST /track/v1/trackingnumbers`, JSON body, up to 30 numbers | `GET /api/track/v1/details/{number}`, headers `transId`, `transactionSrc` |
| Not found | `trackResults[].error.code` `TRACKING.TRACKINGNUMBER.NOTFOUND` | HTTP 404, `response.errors[]`; or `shipment[].warnings[]` |
| Limits | 1,400 calls per 10 s; 100,000 Track calls a day; token endpoint 403 for 10 min when hit too often | none published; 429 "exceeded Quota for the time interval" |
| Sandbox | canned answers for the numbers in FedEx's Postman collection | every number returns the same sample |

### Assumptions to check against the first real answers

Where the documentation does not settle a point, the code takes the reading that cannot show a wrong
"on its way" or "out for delivery", keeps the carrier's own words on the row (*Carrier's Words*) and
keeps the carrier's error code (masked) on *Last Problem*. Each is a one-line change in `client.py`
or `normalize.py` if the first real answers disagree.

1. **FedEx "not found" inside a 200.** The schema implies `trackResults[].error.code`
   `TRACKING.TRACKINGNUMBER.NOTFOUND`; no example shows it. `output.alerts` is read as the documented
   array of `{code, alertType, message}`, and its `TRACKING.DATA.NOTFOUND` is "not found" too.
2. **FedEx `latestStatusDetail.code`** takes the values of the event-code table (FedEx does not say
   so in words). `derivedCode` has no documented values, so it is read only when `code` is not in the
   table. A label-only shipment is assumed to carry `code` OC, so it reads "label created".
3. **FedEx tracking-number length** is not documented; 7 to 34, UPS's documented range, is used.
4. **FedEx's token endpoint answers 403** (for ten minutes) when called too often; a token 403 is
   read as that penalty, not as refused credentials.
5. **UPS "not found"** is the 404 Tracking.yaml documents, in its `response.errors` envelope. A UPS
   shipment with `warnings` and no package is "could not check", since UPS documents no warning codes.
6. **UPS activity letters and numeric status codes** come from the Tracking API appendix, which only
   this build's own research could read (developer.ups.com refuses automated fetches). So "out for
   delivery" also needs UPS's words to say "out for delivery" (037 may be a return leg), and
   "delivered" needs the letter D or 011 with the words saying delivered. Otherwise the letter
   decides, which can only under-state progress.
7. **UPS `expires_in` is a string** (as the OAuth spec types every field); it is cast with `int()`.
8. **Where a package is going**: FedEx's `recipientInformation.address`, then
   `lastUpdatedDestinationAddress`, then `destinationLocation.locationContactAndAddress.address`
   (all in the Track spec); UPS's `packageAddress[]` entry whose `type` names the destination (the
   spec lists the array, not its type values).
9. **Which UPS and FedEx error codes are about the account or the request as a whole**
   (`client.FEDEX_CARRIER_CODE_PREFIXES`, `client.UPS_CARRIER_CODES`), read from the error lists; a
   code outside them on a 400 or 404 is treated as possibly about the number (see above).
10. **The public tracking pages** (`/wtrk/track/?trknbr=` and `/track?loc=en_US&tracknum=`) were
   checked live, not in documentation.

## Turning it on

1. **FedEx**: create an organization and an API project with the Track API (now listed as "Basic
   Integrated Visibility") on [developer.fedex.com](https://developer.fedex.com/). The project's
   test API key and secret work on Sandbox. Production keys come when a FedEx account number is
   attached to the project; the production secret is shown once.
2. **UPS**: on [developer.ups.com](https://developer.ups.com/), sign in with a UPS.com ID, *My Apps*
   → *Add Apps*, link the shipper account, and add the **Tracking** product. The Client ID and
   Secret are under the app's credentials (UPS asks for MFA to show them). Tracking is free for
   shippers. The same credentials work on CIE (Sandbox) and Production.
3. In **Carrier Tracking Settings**: paste each carrier's id and secret, leave the environment on
   **Sandbox**, tick *Use FedEx* / *Use UPS*, then tick *Ask Carriers for Package Status* and save.
   Check the Integrations Health page, then switch each carrier to **Production** with its
   production keys.
