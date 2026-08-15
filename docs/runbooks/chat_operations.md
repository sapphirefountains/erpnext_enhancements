# Chat operations runbook

Phase 6 §4.J. Written for whoever is on the end of the phone, not for whoever wrote the sync
engine. Every switch below is a field on **Chat Settings**.

Companion documents: [`docs/nik-runbook.md`](../nik-runbook.md) for the platform approvals and
the decisions only Nikolas can make; [`erpnext_enhancements/chat/README.md`](../../erpnext_enhancements/chat/README.md)
for how the parts fit together.

---

## 1. The rollback table

**Layers, outermost first.** Turn off the outermost one that fixes the problem — each row below
it stays as it was, so you can back out one layer at a time instead of switching the feature off
because one part of it is misbehaving.

| # | Switch | Blast radius | What is lost | Proof it took effect |
|---|---|---|---|---|
| 1 | `enabled` = 0 | **Everything.** No chat surface renders, no endpoint answers, no job runs | Nothing is deleted. People lose access to conversation they can still see in the native Google Chat client | Open `/chat` as anyone: refused. `bench execute …chat.health.report` prints `enabled 0` in CONFIGURATION |
| 2 | `google_sync_enabled` = 0 | ERPNext-only chat. The SPA works; nothing reaches Google | Messages sent in this window never appear in the native client, and never will — there is no backfill | Send a message, then check `Chat Relay Job` — **no row is created at all**, not a Skipped one. See §2 |
| 3 | `relay_outbound_enabled` = 0 | ERPNext → Google only | Same as row 2 in the outbound direction; inbound still arrives | Same check. Inbound events keep landing in `Chat Inbound Event` |
| 4 | `relay_inbound_enabled` = 0 | Google → ERPNext only | Events are still **received and stored**, just not applied. Turning it back on does not replay them — the reconciliation sweep does | `Chat Inbound Event` rows accumulate at `Received` and no `Chat Message` appears |
| 5 | `restrict_to_whitelist` = 1 + trim `Chat Allowed User` | Individual people | Nothing. They keep the pre-existing Triton widget | The removed person gets *"Chat is not open to your account yet"* on `/chat` **and** on a direct attachment link — the deep-link case §4.J is worded about |
| 6 | `drift_detection_enabled`, `alerts_enabled` | Observability only | You stop hearing about problems; the problems continue | `chat.health.report` prints the switches |

### The layer that is not reversible in place

**Deleting a Workspace Events subscription cannot be undone.** Google deletes an expired or
removed subscription permanently — there is no reactivate. Recreating one gives you a *new*
subscription that delivers from the moment it exists, so **everything said in the gap is
missing and nothing in Google will ever tell you what**.

If it happens, the recovery is the reconciliation sweep, which asks each space directly for
messages created since the room's watermark:

```bash
bench --site <site> execute erpnext_enhancements.chat.sync.reconcile.reconcile_due_rooms
```

It runs hourly anyway (`50 * * * *`) and takes 25 rooms per pass, so a large estate takes
several hours to come round. It recovers **creations only** — a message deleted during the gap
is absent from a `createTime` listing and indistinguishable from one outside the window.

---

## 2. The degradation contract

**With Google sync off, everything except the native client keeps working, and no relay job is
created at all.** That last clause is the testable one, and it is already how the code behaves:
`sync/outbox.relay_disposition` returns *no row* — not a `Skipped` row — when `enabled`,
`google_sync_enabled` or `relay_outbound_enabled` is off.

The distinction matters because a `Skipped` row would look like a queue that had considered the
message and declined, and would accumulate a backlog nobody drains. No row means the message is
simply ERPNext-only, which is exactly what the switch promises.

What to check during the drill:

1. Send a message. It appears immediately for both users. **No spinner persists.**
2. `Chat Relay Job` gains no row for it.
3. Edit and delete it. Same: no rows.
4. Turn `google_sync_enabled` back on.
5. Send another message. **Now** a relay job appears and drains.
6. Confirm the backlog drains **in `job_seq` order**, with zero duplicates and zero rows in
   `Dead`.

Step 6 is the one worth watching: a room drains at one write per second by design, so a room
that accumulated 600 messages takes 600 seconds and **that is the system working correctly**.

---

## 3. The five most likely incidents

### 3.1 "Chat has gone quiet" — nothing arrives from Google

A subscription can sit `ACTIVE` with a healthy expiry and deliver nothing at all, so start with
the sweep rather than the subscription list.

```bash
bench --site <site> execute erpnext_enhancements.chat.health.report
```

Look at `OPEN ALERTS` first — it is printed above the numbers for this reason. `inbound` or
`subscriptions` alerts name the cause. If the board is empty, look at `last_event_at` per room
in the ROOMS panel: a room with recent traffic in the native client and an old `last_event_at`
is the §4.J alarm, and the drift census reports it as `reconcile_stale`.

### 3.2 The queue is not draining

`chat.health.report` → QUEUE. The number that matters is **oldest *due* Pending job**, not
oldest Pending: a job deferred by ordering or backoff is healthy. Over ten minutes means nothing
is draining; over an hour means the worker is dead, the kill switch is on, or a deploy flushed
the queue Redis.

**A deploy flushes the queue Redis.** Jobs enqueued before it are gone. This is known
(`deploy-flushdb-destroys-queued-jobs`) and the relay rows survive it — the sweeper reclaims
them — but any *other* background job queued at that moment does not.

### 3.3 A message is in ERPNext and not in Chat, or the reverse

Run the drift census:

```bash
bench --site <site> execute erpnext_enhancements.chat.governance.drift.report
```

It reports five classes, each on **evidence that the mirror acted** — a dead relay job, a
completed job whose resource name never came back, an abandoned inbound event. It refuses to run
unless chat is actually enabled, because on a dormant site every message looks drifted.

`inbound_abandoned` is the actionable one: the payload is still on the row, so setting
`Chat Inbound Event.status` back to `Received` replays it.

### 3.4 Somebody says they are not getting notifications

Almost always presence, not notifications. A client that believes you are sitting focused on a
conversation suppresses every ping for it.

```bash
bench --site <site> execute erpnext_enhancements.chat.notifications.debug.explain --kwargs "{'user': 'someone@sapphirefountains.com'}"
```

A suppressed notification produces no error and no log line, which is why this command exists.

### 3.5 The audit chain fails verification

You will hear about this as a `Critical` alert, per chain, from the nightly verifier.

**A break is tampering or corruption. It is not something the application can cause** — every
writer is append-only and guarded. Every row *after* the first break is suspect and every row
*before* it is not, and the alert names the first break by name.

Do not attempt to repair it. There is no re-anchor, deliberately: a chain that can be re-anchored
is one that can be re-anchored by whoever broke it.

---

## 4. The pilot

### 4.1 Composition — five to eight people, for two weeks

Composition matters more than size. You need, at minimum:

- **someone who lives in the Google Chat app on a phone** and rarely opens ERPNext — they test
  the half of the system that ERPNext cannot see;
- **a desktop-only user** who will never open the native client;
- **someone who will hammer the assistant**;
- **someone from accounting**, whose rooms should be invisible to the others — they are the
  permission-boundary reality check, and the only one who can tell you it failed;
- **the person who will own operations afterwards**.

### 4.2 The constraint that shapes the whole rollout

**Chat apps are enabled at the *top* organizational unit, so a pilot-OU rollout is impossible.**
The two controls that do work are the Chat app's visibility setting pointed at a `chat-pilot@`
Google Group, and the ERPNext-side `Chat Allowed User` whitelist.

**Both must name the same people.** A mismatch produces the confusing failure where the
assistant answers from the web app and not from the phone, or the reverse — and the person
reporting it will describe it as "chat is broken", not as "my two memberships disagree".

Check the ERPNext side with:

```bash
bench --site <site> execute erpnext_enhancements.chat.rollout.erpnext_link_report
```

### 4.3 The note to send before they start

Draft below; **Nikolas sends it.** This is not politeness — it is what makes the governance
decisions in this phase real. Somebody who was not told cannot have consented.

> Subject: Before you start on the new chat — what it does with your messages
>
> You are one of a handful of people trying the new chat over the next fortnight. Before you
> send anything, four things you should know, because they are all true and none of them is
> obvious from the interface.
>
> **An administrator can read any conversation, including your direct messages.** That has
> always been true of company systems; what is new is that here it is *recorded*. Every such
> read writes a permanent entry saying who read what, when, and the reason they typed — and
> they cannot proceed without typing one.
>
> **You can see those entries.** There is a view that shows you who has read your messages and
> why. It names the person, not "an administrator".
>
> **The assistant reads across your rooms.** When you mention `@triton`, it searches
> conversations *you* have access to in order to answer — so an answer may draw on a room other
> than the one you asked in. It never reaches anything you could not open yourself.
>
> **Nothing is deleted.** The current retention setting keeps messages indefinitely. Deleting a
> message hides it from the transcript; the original text is retained and an administrator can
> reveal it, which is also recorded.
>
> If any of that is uncomfortable, say so before you start rather than after. That is what the
> pilot is for.

### 4.4 The acceptance checklist

Twenty-four steps, no jargon and no bench commands — a non-engineer runs this. **Four of them
are the gate**, marked ★. If any ★ fails, the pilot does not proceed.

| # | Do this | Expect |
|---|---|---|
| 1 | Open ERPNext and find the chat icon | It opens without an error |
| 2 | Open a conversation with one other person | Past messages load oldest at the top |
| 3 | Send "hello" | It appears immediately, with no spinner left behind |
| 4 | Open Google Chat on your phone, find the same conversation | The message is there |
| 5 | ★ Look at who it says sent it | Your **real name**, not an email address or "bot" |
| 6 | Reply from the phone | It appears in ERPNext within a few seconds |
| 7 | Edit your phone message | The edit appears in ERPNext |
| 8 | Delete your phone message | It disappears from the ERPNext transcript |
| 9 | Edit a message in ERPNext | The edit reaches the phone |
| 10 | Send a message with an attachment | The other person can open it |
| 11 | Send a message mentioning the other person by `@name` | They get a notification |
| 12 | Have them open the conversation and leave it on screen | — |
| 13 | ★ Send them another message | They get **no** notification — they are looking at it |
| 14 | Have them switch to another tab | — |
| 15 | ★ Send another | Now they **do** get one |
| 16 | ★ Mention them by name while they are looking at the conversation | They get one anyway — a mention beats being present |
| 17 | Reply inside a thread | The reply stays in the thread on both sides |
| 18 | From the phone, mention `@triton` with a question about a project | — |
| 19 | ★ Wait up to a minute | It answers **in the same thread**, not in the main room |
| 20 | Ask it something needing a different conversation you are in | It answers, and says where it looked |
| 21 | Ask it about a conversation you are **not** in | It says it cannot find anything — not "access denied" |
| 22 | ★ Ask the accounting tester for the name of one of their rooms, then search for it | **Nothing.** No result, no title, no hint it exists |
| 23 | Close everything, wait ten minutes, reopen | Unread counts are right |
| 24 | Report anything that felt wrong, even if it worked | — |

Steps 5, 13/15/16, 19 and 22 are the gate. In plain terms: **messages are attributable, silence
is deliberate rather than broken, the assistant works where people actually are, and the
permission boundary holds.** A pilot that proceeds past a failure in any of those is a pilot
that will produce a confident wrong conclusion.

---

## 5. What this runbook cannot tell you

Stated so a green pass is not over-read.

- **The Chat-dark drill needs two real people and thirty minutes of production.** The
  automated half proves no relay row is written; it cannot prove the experience is acceptable.
- **The notification matrix needs two browser profiles and a phone.** Steps 13–16 above are
  the minimum, not the matrix.
- **Fifteen of Phase 6's named tests need a real bench**, which CI does not have and will not
  get. Three bench suites from earlier phases have never been executed at all.
- **Whether Cloud Armor sits in front of this app has never been read.** Two documents claim
  opposite things, one read-only `gcloud` command settles it, and OWASP preconfigured rules
  false-positive on user-typed chat text — so a coworker pasting a SQL snippet gets a 403 with
  no feedback.
