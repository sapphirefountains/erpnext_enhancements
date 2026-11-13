# `carrier_tracking/` — FedEx and UPS package status

Put a package's tracking number on the purchase order it belongs to, and ERPNext asks FedEx or
UPS where it is and when it should arrive, so nobody has to go to the carrier's website
(ER-2026-1333277, from the warehouse lead, 2026-11-13).

**Off until set up.** Nothing is asked of any carrier until a System Manager enters API
credentials in **Carrier Tracking Settings**, turns that carrier on, and turns on *Ask Carriers
for Package Status*. See *Turning it on* below.

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
| `package_rules.py` | Adding a number: cleaning it, the carrier choice (Auto, FedEx, UPS, Other), the tracking page, and for Other or an unknown carrier the "recorded, not checked" state. `MANAGE_ROLES`, the permission rule below. No Frappe |
| `poll_rules.py` | When a package is checked and what an answer does to it: due rows and their order, the limits (constants, each explained), the two kinds of failure, and every reason polling stops. No Frappe |
| `tracking.py` | The hourly `poll` (bounded, silent while off, never raises), `refresh_order` (a person's Refresh, once a minute per order), and the single writer of a row's carrier fields (`db.set_value`, no `modified` bump) |
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
a week without ever being found, unreadable 12 times in a row, unchanged for 21 days, or tracked
for 90 days. A failure about the carrier (refused login, "too many requests", down) never counts
against a package: that carrier is skipped for the rest of the run, and every package carries on
when it clears. "Could not check" never changes a stored status. Each answer is committed on its
own, so a deploy that flushes the queue costs one run; the next run picks up where it left off. The
reasoning for each number is in `poll_rules.py`.

## On the Purchase Order

`public/js/po_package_tracking.js` puts a compact banner at the top of the order (draft or
submitted, never new or canceled): each tracking number with the carrier's status in plain words,
the carrier's estimated arrival beside the purchaser's own *Expected Delivery Date*, when it was
last checked, a link to the carrier's tracking page (a new tab), and *Remove* for those who may.
*Add tracking number* opens a small dialog (number, carrier Auto/FedEx/UPS/Other, and a link for
Other). *Refresh* asks the carriers now. When live status is off, the banner says so and the links
still work. Nothing on the banner writes to the order, so the form never turns "Not Saved", and
nothing changes the route, so Back and Forward behave.

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

Places where the documentation left a question, so the first real call should be watched:
FedEx does not show an example of a not-found number inside a 200 response (the schema implies
it); FedEx does not say its event-code table is exactly the set of `latestStatusDetail.code`
values; UPS publishes no numeric code for "not found" (the client relies on the 404). Each would
be a one-line change in `client.py` or `normalize.py`, and the carrier's error code (masked) is
kept on the row's *Last Problem* field to say which.

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
