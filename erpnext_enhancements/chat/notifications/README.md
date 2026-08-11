# Chat notifications

The phase where **every failure is invisible by construction.** Nobody reports the ping they
did not get. A suppressed message produces no error, no log line and no complaint until
somebody says "I never got that" three days later — and a correctly-suppressed message looks
identical, from every angle, to a silently broken one: the sender sees a delivered message,
the recipient sees nothing, the push service returns 201, the server logs a success.

Everything below is shaped by that.

---

## Why there is no FCM, and why adding it would not survive a deploy

Frappe v16 ships `frappe.push_notification.PushNotification`, and this site has
`Push Notification Settings.enable_push_notification_relay = 1` with both credentials filled
in. **It is configured-looking and broken**, in the worst available way:
`push_relay_server_url` is `null` in site config, while `is_enabled()` reads only the DocType
flag — so it returns `True`, then hands a `None` URL to its HTTP client and fails at request
time. It is also Google-Cloud-Messaging only and needs a relay service we do not run.

The libraries that would normally do this are **measurably absent** from the production
bench. A live import probe returned `ModuleNotFoundError` for `pywebpush`, `py_vapid`,
`ecdsa` and `http_ece`. And the deploy pipeline is exactly:

```
git fetch/reset → bench --site all migrate → bench build → FLUSHDB → restart
```

There is **no `pip install` step anywhere in it.** A dependency added to `pyproject.toml`
would not be installed by a deploy and would be lost on any VM rebuild from the startup
script. `cryptography` (46.0.7) and `PyJWT` (2.13.0) are present and are the whole toolkit.

So `webpush/` is ~200 lines of hand-rolled authenticated crypto, in the same spirit and for
the same reason as `stripe_payments` hand-rolling `Stripe-Signature` verification and
`quickbooks_online` hand-rolling its OAuth client. **This is an argued position, not an
oversight.** The next person's instinct will be to fix it by adding a library; the fix would
not survive the next deploy.

---

## The modules

| File | What it is |
|---|---|
| `policy.py` | The twelve-row suppression table as **one pure function**. No Frappe, no database, **no clock** — `now` is a parameter. Everything else is wiring around it. |
| `presence.py` | The Redis heartbeat store. A heartbeat with an expiry, never a flag set on connect and cleared on disconnect. |
| `settings.py` | `Chat Settings` → a clamped `Policy`. The only place the three tunables are read. |
| `bell.py` | `Notification Log` rows, deduped per unread cycle, with the email path structurally impossible. |
| `fanout.py` | One decision per member, off Phase 2's `notify_new_message` seam. Runs in a background job. |
| `read_state.py` | The four-part cross-surface read sync, including the step everyone forgets. |
| `webpush/` | VAPID and `aes128gcm`. See above before concluding it should use a library. |
| `api.py` | The whitelisted surface. **Not one endpoint takes a user parameter.** |
| `debug.py` | `explain(user, room)` — why this person would or would not be notified, with the reason code. |

---

## The rules that are easy to get wrong

**The client reports; the server decides.** That is invariant I6. A notification suppressed by
a client that simply declines to render it has been suppressed by something nobody can audit,
test or explain. The heartbeat carries facts — which room, whether the window is focused — and
every "should this fire" answer is computed server-side from them.

**Presence is a heartbeat with an expiry, never a sticky flag.** A browser crash, a `SIGKILL`,
a closed laptop lid and a network partition all produce **no disconnect event**. A design that
sets `online = 1` on connect and clears it on disconnect leaves that person permanently
present-and-focused and silently drops every message to them, forever, until they next open
the app. `test_presence_expiry_resumes_notifications` stops the heartbeat with no cooperation
of any kind from the client and asserts the decision flips on its own.

**A missing signal notifies.** Presence lives in Redis and every deploy `FLUSHDB`s it. An
unreadable or empty store resolves to `PRESENCE_UNKNOWN`, which **notifies**. The two failures
are not symmetric: a duplicate ping is visible, self-correcting and reported by the person who
got it; a suppressed message is invisible to everyone including the person who needed it.
Under a Redis outage everyone is notified about everything — loud, and survivable. The
opposite rule turns the same outage into an unannounced total blackout.

**`focused_changed_at` is server-stamped.** A client that could supply it could backdate its
own blur and buy silence indefinitely. Stamping it on receipt bounds a hostile or wedged
client to suppressing its **own** notifications, which is self-harm.

**No message text ever enters a `Notification Log` row.** `Chat Message` ships zero DocPerm so
bodies are unreachable through `/api/resource`, the desk, global search and the generic MCP
tools. A snippet in a notification subject would move employee-private conversation into a
table with ordinary permissions and undo all of it. Subjects name a sender and a room — which
is also why they never go stale, since there is no count in them to be wrong.

**The badge reconciles wholesale.** `get_unread_state` is the authority; realtime events are
an accelerator. Realtime is fire-and-forget, so a client that merges deltas is wrong by
exactly the number of events it missed while disconnected — permanently, because nothing
corrects it. The visible form is a bubble showing five over rooms summing to three.

---

## Two things that bit during the build, recorded so they do not bite again

### `dedupe_on` does not do what the plan assumed

ADR §H.6 specifies `enqueue_create_notification(..., dedupe_on=["document_type",
"document_name"])` for one bell row per (user, room). Frappe's own docstring, read from the
deployed v16 build:

> skip creation if a Notification Log already exists matching those field values (prevents
> duplicate rows — **it does not re-surface an existing notification**)

and `_notification_exists` is a `frappe.db.exists` on `for_user` plus those fields **with no
`read` filter**. Under that design: first message writes a row → the person reads the room →
the row goes `read = 1` → every later message matches the existence check and writes nothing.
**That room's bell would be dead for the life of the row.**

So the dedupe is ours and is scoped to the *unread* row (`bell.has_unread_row`). Volume stays
bounded because opening a new cycle requires a human to have read the previous one. Residual:
two messages in the same instant can both write a row — bounded at two, cleared by the next
read, and closing it would mean a lock on the notification path.

### The service worker could not live at root scope

A registration is keyed by **(storage key, scope URL)**, and registering an identical scope
*replaces* the existing registration. `kiosk-sw.js` and `wall-sw.js` both already register at
`/`, so root was never available — and, incidentally, those two are already competing for that
one slot today, each with an `activate` handler that deletes every cache in the origin that is
not its own.

`chat-sw.js` therefore registers at `/chat/`. Narrowing needs no `Service-Worker-Allowed`
header, and it costs nothing that matters: a push subscription belongs to the **registration**,
not to the pages it controls, so the worker receives push for the whole origin while
controlling only `/chat/`. It also caches nothing, has no `fetch` handler, and deletes no
caches.

The sharp edge for callers: `getRegistration()` with no argument resolves against the current
page, so from a Desk page it returns the kiosk or wall worker instead. Every lookup passes the
scope.

---

## Operating it

Push ships **off**. To turn it on:

```bash
bench --site <site> execute erpnext_enhancements.chat.notifications.webpush.vapid.generate
```

It prints two lines for `sites/<site>/site_config.json` and writes nothing itself. Add them,
restart the bench, then tick `Chat Settings → Web Push Enabled`.

**Running `generate` again on a site that already has a key is destructive** and it refuses
for that reason: every browser subscription is bound to the public key it subscribed with, and
the push service answers **403** for the old ones rather than 410 — so they are never pruned
either, and every notification spends a request per dead device forever.

The private key lives in `site_config.json` rather than on a DocType because
`test_chat_guardrails` forbids a `Password` field on any chat DocType, on the grounds that
*"once it is in `__Auth` it is in every backup"*. `site_config.json` satisfies that concern
rather than evading its wording — it is not in a database backup at all.

### When somebody says notifications are broken

```bash
bench --site <site> execute erpnext_enhancements.chat.notifications.debug.explain \
  --kwargs '{"user": "jane@example.com", "room": "<room name>"}'
```

It returns the tunables in force, **every** presence record with a live/stale verdict per
record (the stale ones matter — a row of expired tabs is the commonest cause of "it suddenly
started notifying me about everything"), the classified state, and the decision for both an
ordinary message and a mention. It reads only; running it during an incident cannot make the
incident worse.

---

## Open questions for a human

**Does a mention pierce a muted room?** The ADR contradicts itself. §H.1 row 9 lists the rows
a mention overrides and row 11 (mute) is not among them; §H.2.3 says so in terms and defers
the argument to CQ-8. CQ-8's own ship-default (a) then says *"soft mute … mentions STILL
NOTIFY"*. Both cannot be built.

Shipped: **mute wins**, because §H.7 hands Phase 4 the twelve-row matrix as invariant I7's
test and a table contradicting its own test is worse than either answer. The other reading is
`Chat Settings → A Mention Pierces A Muted Room`. Either way a muted room still shows its
unread dot.

**iOS.** Web Push on iOS requires the site to be added to the Home Screen and launched as a
standalone app; a page open in a Safari tab cannot receive push. iPhone users get the bell,
the badge and the in-app experience but not the banner. That is a rollout-communications fact,
not an architecture one.

**Notification Log has no retention.** 9,714 rows over thirteen months (measured 2026-08-11),
no `Logs To Clear` row, registered in neither Frappe's hooks nor ERPNext's. That is CQ-21 and
the ADR asks for it as its **own PR**, so it is deliberately not in this branch.
