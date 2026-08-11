# The chat client

ADR 0009 Phase 3. Two consumers, one set of modules:

* **the SPA** at `/chat` — a website route, served by `www/chat.html`, entry
  `public/js/chat.bundle.js`;
* **the floating bubble** on every Desk page — `global_enhancements/triton_widget.js` plus
  `global_enhancements/chat_surface.js`, shipped inside `erpnext_enhancements.bundle.js`.

They share `transport`, `handoff`, `optimistic`, `signals`, `mentions`, `citations` and
`dom`, so the two halves cannot disagree about the API shape, the idempotency rule, the
handoff record or the read-state semantics. What the bubble deliberately does **not** pull in
is `app.js`, `socket.js`, `virtual_list.js` and `message_view.js` — see the size table below.

## No Vue, and that is the design

The repo vendors a UMD Vue that sets `window.Vue` through `app_include_js`, i.e. on every
Desk page. The two-Vue-copies hazard is two runtimes on **one document**: components created
by one, reactive proxies from the other, `provide`/`inject` crossing the boundary, and a
doubled bundle. It presents as "the widget stopped updating after I opened chat", six weeks
after the change that caused it.

Phase 3 closes it structurally rather than by bundler configuration
(§4.1 option (c)): this client is plain DOM, the SPA bundles no Vue at all, and expanding the
bubble **navigates** to `/chat` rather than mounting the SPA into a Desk page. So the Desk
document has `window.Vue` and no SPA, and the SPA document has the SPA and no Vue.

`scripts/test_chat_source_rules.js` fails the build if either bundle grows a `vue` import or
a `createApp` call. "We were careful" is not a guard; a dependency added a year from now
would undo it.

## No `innerHTML`

Not "no `innerHTML` with user data" — **none at all**, so the rule needs no judgement call at
the call site and can be enforced by a source scan. Every message body, sender name, room
title, filename and citation label in this application is written by an employee and rendered
to every other employee; one careless template literal is a stored-XSS vector with a straight
path between them. Build nodes with `dom.js`'s `el()` / `fill()`.

The one place HTML is still parsed is the **Triton** bubble, where `frappe.markdown` renders
the answer exactly as it did before this phase. Inline citations are applied to that
renderer's **output** by walking text nodes, so the sanitiser policy Appendix A records sees
precisely the string it saw before and is untouched.

## The modules

| Module | Pure? | What it is |
|---|---|---|
| `routes.js` | **yes** | Route parse/build and the three-tier restoration resolver. `buildRoute` must produce the same bytes as `chat/links.py::build_chat_route`; both sides assert the same table. |
| `handoff.js` | mostly | The bubble→SPA record. `sessionStorage` primary (per tab, survives a same-tab navigation), `localStorage` mirror with a nonce and a 60 s TTL for the new-tab case, consumed and deleted on read. |
| `citations.js` | **yes** except `applyCitations` | `[[ref:N]]` tokenizing, the streaming tail buffer, and the DOM applier. |
| `optimistic.js` | **yes** | The pending-message store, keyed on `client_message_id` throughout. |
| `signals.js` | **yes** | Read batcher, typing throttle and registry, presence union, and `ensureClientId` — **the one presence-client id generator**. It lives here rather than in whichever surface needed it first because the union above is keyed on it, and a second generator is a second way for three tabs to look like one person, or one to look like three. |
| `mentions.js` | **yes** | Trigger detection, insertion, re-anchoring, and the payload. |
| `dom.js` | partly | Node builders, linkification, relative time, grouping, and `isComposingKey` — **the one IME rule every Enter-to-send handler consults**. Four copies of that rule is four chances for the next one to carry half of it, which is exactly what happened before it was extracted. |
| `transport.js` | no | `fetch` against `/api/method/…`. No `frappe.*` — the SPA has no Desk bundle. |
| `socket.js` | no | The socket.io connection, `doc_subscribe`, and the reconnect resync. **SPA only.** |
| `virtual_list.js` | no | Windowed transcript with dynamic heights, anchored on a message docname. **SPA only.** |
| `message_view.js` | no | The one message component, every surface. **SPA only.** |
| `app.js` | no | Layout, router, and the wiring. **SPA only.** |

The pure ones carry the logic on purpose: this repo has no Frappe integration-test job in CI,
so the bench-free tier is the only one with automatic regression protection, and anything that
matters is pushed into a function a plain `node` script can call.

## Two rules the first day of pilot use bought

Both of these shipped through review and a green suite, several times each, and both are now
enforced by `scripts/test_chat_source_rules.js` rather than by remembering.

**A surface that reads shared state must also write it.** The bubble and the SPA are two
clients of one server, and every time a feature was wired into only one of them the result
was silent: the bubble listened for room events and never joined the room, so it received
nothing forever; it read the tier-3 restoration hint that only the SPA wrote, so `/chat`
always opened cold; and it published typing but never presence, so its users rendered offline
next to their own live typing indicator. None of these throw, log or degrade visibly — they
look exactly like a quiet room, a first visit, and a colleague who stepped away.

**Emptying the transcript is not a state.** `setItems([])` leaves the virtual list holding
two zero-height spacers, which reads as a broken page rather than as "nothing here". Three
separate panes did this — the cold empty state, a search with no hits, and a query the server
rejected as too short. `showPlaceholder` is now the one writer, and a pane that clears the
transcript without reaching it fails the build.

## Size

Measured 2026-08-10 on raw source, and again with comments stripped, because these files are
deliberately comment-dense and only the code ships after esbuild.

**The Desk bundle is the one that matters** — it loads on every ERPNext page for every user,
so a regression there taxes the whole system rather than just chat.

| | raw | code only |
|---|---|---|
| `triton_widget.js` before | 47,404 | 38,888 |
| `triton_widget.js` after | 60,328 | 45,143 |
| shared modules newly pulled into the Desk bundle | 92,664 | 51,594 |
| **added to `erpnext_enhancements.bundle.js`** | **+105,588** | **+57,849** (~14 KB gzipped) |

SPA-only modules, which cost the Desk **nothing**: `app.js` (56,483), `message_view.js`
(13,353), `virtual_list.js` (8,926), `socket.js` (7,161).

~14 KB gzipped on every Desk page buys the unread badge, the coworker surface and the
handoff. If that ever needs to come down, the lever is a dynamic `import()` of
`chat_surface.js` on first switch to the Chats tab — the badge needs only `transport.js`.
That was not done now because a dynamic import inside an esbuild bundle produces a second
chunk, and this repo's asset pipeline resolves exactly one filename per bundle through
`assets.json`.

## Running the tests

```bash
node scripts/test_chat_citations.mjs
node scripts/test_chat_client_logic.mjs
node scripts/test_chat_source_rules.js
```

No runner and no `npm install`, which is the same shape as the repo's three existing JS
guards and the reason they run at all. The first two are `.mjs` because the code under test
is ESM; node prints a `MODULE_TYPELESS_PACKAGE_JSON` warning about the imported `.js` files.
**Do not silence it by adding `"type": "module"` to `package.json`** — that would turn
`test_pick_routing_lines.js`, `test_address_components.js` and `test_chat_source_rules.js`
into parse errors.
