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
| `doctype/carrier_tracking_settings/` | **Carrier Tracking Settings**, a Single: the master switch, and per carrier a switch, Sandbox or Production, a client id and a client secret (Password). System Manager only. Nothing in the app writes to it |

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
