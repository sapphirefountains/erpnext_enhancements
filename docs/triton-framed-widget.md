# The framed Triton widget: the Desk's half

**Contract version:** 1

Since v1.802.0 the Triton assistant on Desk pages can be shown two ways. **Classic** is the
widget this app has always had: a chat program written in the Desk
(`public/js/global_enhancements/triton_widget.js`) that talks to Triton through a relay on
ERPNext's server (`triton_chat.py`). **Framed** keeps the same round button and the same panel,
and shows Triton's own chat page in a frame inside it, so the Desk and the Triton site are one
chat program and a Triton feature is built once.

Classic is the default. Nobody is on the framed widget until *Triton Assistant Settings > Widget*
is changed.

This page is the Desk's account: what this app builds, what it does with each message, and how
to run a pilot. **The agreement between the two sides is Triton's document,
`docs/embed-bridge.md` in the `sapphirefountains/triton` repository.** If the two disagree, that
one is right and this page and `public/js/triton_frame/contract.js` are wrong.
`scripts/test_triton_frame.mjs` fails when the version above or a message name below is not the
one in `contract.js`.

- [Who gets which widget](#who-gets-which-widget)
- [Running a pilot](#running-a-pilot)
- [What the Desk builds](#what-the-desk-builds)
- [Signing the frame in](#signing-the-frame-in)
- [Messages](#messages)
- [When the classic widget takes over](#when-the-classic-widget-takes-over)
- [What the framed widget does not do yet](#what-the-framed-widget-does-not-do-yet)
- [Where each piece lives](#where-each-piece-lives)
- [Tests](#tests)

## Who gets which widget

*Triton Assistant Settings*, section **Framed Widget (pilot)**:

| Field | What it does |
|---|---|
| **Widget** | *Classic*: everyone gets the classic widget. *Framed for pilot users*: only the people in the table get the framed one. *Framed for everyone*: everyone who can see the assistant does. |
| **Pilot Users** | The people on the framed widget while *Widget* is *Framed for pilot users*. |
| **Framed Site Address** | Leave blank. The frame loads from the Gateway URL in *Triton Settings*, which on production is the Triton site itself. Set it only for a site whose Gateway URL is an internal address a browser cannot open. |

The master switch and the allow-list at the top of the same page still decide who sees the
assistant at all. A change takes effect the next time a person loads the Desk: saving the
settings clears every person's cached boot payload (`TritonAssistantSettings.on_update`), which
Frappe otherwise keeps until something else clears it.

The answer reaches the page as `frappe.boot.triton_frame` (`{origin, url, page_context}`), and
only for a person on the framed widget. The browser never reads the settings.
`api/triton_frame.py` `frame_config()` decides, and it answers Classic whenever anything is
unclear: no mode stored, a mode it does not know, no usable Triton address (it must be `https`),
no shared secret, a person outside the allow-list.

## Running a pilot

Triton must serve the framed page, and it does: Triton 0.88.0 has been live since 2026-11-12
with `/embed.html` and the ticket route.

1. Check Triton still serves the page, and lets only this Desk frame it:

   ```bash
   curl -sI https://triton.sapphirefountains.com/embed.html | grep -i -E "content-security-policy|x-frame-options"
   ```

   Expect `frame-ancestors https://erp.sapphirefountains.com` and no `X-Frame-Options` line.
2. Open **Triton Assistant Settings**, section **Framed Widget (pilot)**.
3. Set **Widget** to **Framed for pilot users**, add the pilot people to **Pilot Users**, leave
   **Framed Site Address** blank, and save.
4. Each pilot person reloads the Desk. The button looks the same. The panel now shows Triton's
   page, with Triton's own header.
5. To check a person is on it, in their browser console: `frappe.boot.triton_frame` is an object.
   A console line starting `[Triton] The framed assistant is not available` means they were handed
   back to the classic widget for that page load, and says why.
6. To stop, set **Widget** back to **Classic**. Each person is on the classic widget again at
   their next Desk load. Nothing else needs undoing.

First things to try in a pilot, because they could not be checked without one: the on-screen
keyboard on a real iPhone and a real Android phone (the message box must stay above it),
dictation, and copying a reply.

## What the Desk builds

A button, a panel, one frame, and the Desk's half of the bridge.

- **The button and the panel are the classic ones.** They carry the classic classes
  (`triton-fab`, `triton-panel`), so position, the phone sheet, the lift above the quick bar and
  above the Guided Entry footer, and the open animation come from the classic stylesheet.
  Alt+T opens and closes the panel, and `SapphireTriton.ask(prompt, context)` still works from any
  Desk page.
- **One frame**, `<iframe allow="clipboard-write; microphone" referrerpolicy="origin">`, created
  the first time the panel is opened. `camera` and `geolocation` are not in `allow`, and there is
  no `sandbox`.
- **The frame's address is assigned once**, before the frame joins the page, and never again.
  That is what keeps chat navigation out of the tab's Back and Forward list. The frame is never
  reloaded, navigated or moved to another parent, and closing the panel only hides it, so the
  chat is where it was left and no new ticket is spent.
- **The phone sheet is sized to the visible screen** (`window.visualViewport`). A framed page
  cannot see the on-screen keyboard; the Desk can, so it shrinks the sheet.
- **Loaded only when needed.** The loader is its own bundle (`triton_frame.bundle.js`), fetched
  only for a person on the framed widget. The Desk's main bundle carries a small gate
  (`triton_frame_gate.js`) that reads the boot key and, on the classic widget, does nothing else.

ERPNext sends no `Content-Security-Policy` and no `Permissions-Policy` (production, read
2026-11-11), so nothing on this side blocks the frame or the microphone. If either header is ever
added, `frame-src` must include Triton's origin and `microphone` and `clipboard-write` must not be
forbidden to it.

## Signing the frame in

The frame has no Google step. ERPNext vouches for the person with a ticket that works once.

1. The frame says `ready`.
2. The Desk page calls `erpnext_enhancements.api.triton_frame.get_ticket` (POST, with the
   person's session and CSRF token).
3. That method refuses a Guest and anyone who is not on the framed widget, then asks Triton,
   server to server, with the Admin Webhook Secret from *Triton Settings*:
   `POST {Gateway URL}/api/v1/auth/erpnext-bridge/embed-ticket` with `{email, full_name}`.
4. Triton answers `{ticket, expires_in: 60}`. Only the ticket goes back to the page.
5. The Desk posts `ticket` to the frame. The frame trades it at Triton for its usual 30-minute
   token, keeps that in memory, and says `signed-in`.

Every half hour the frame says `need-ticket` and steps 2 to 5 repeat. The person sees nothing.

What the method answers when it cannot give a ticket, and what the person is told (the sentence
goes to the frame as `ticket-error`, and the frame shows it as written):

| Triton answered | Reason | Sentence |
|---|---|---|
| nothing, a 5xx, or something that is not a ticket | `unreachable` | Triton could not be reached. Try again in a minute. |
| 401 | `setup` | Triton is not set up correctly. Tell an administrator. |
| 400 or 403 | `refused` | Triton does not accept your ERPNext account. Ask an administrator to check your Triton account. |
| 404, 405 or 501 | `unsupported` | (none: this Triton has no ticket route, so the classic widget takes over) |
| more than 20 requests a minute from one person | `busy` | Triton was asked to sign you in too many times. Wait a minute, then try again. |

A 503 is with the 5xx answers on purpose. Triton's contract gives it one meaning (no gateway
secret configured) and Triton itself also answers 503 when its database is unavailable; the two
cannot be told apart without reading the reply's body, which this app never does.

Neither the secret nor a ticket is logged or cached. With *Debug Logging* on, a failure writes the
HTTP status to the Error Log and nothing else. The server gives Triton 8 seconds, because the
frame waits 15 for a ticket and the person should read this app's sentence, not the frame's guess.

## Messages

Every message, both ways, is `{ "triton": 1, "type": "<name>", ...fields }`.

Three rules hold for all of them:

- **One sender, one receiver.** The Desk handles a message only when its origin is Triton's
  configured origin **and** its source is this page's own frame. It posts with that origin as the
  target, never `"*"`.
- **Nothing before `ready`.** The Desk says nothing to the frame, and acts on nothing from it,
  until the frame has said `ready`.
- **Unknown is ignored.** A message type, a field or a value the loader does not know is dropped
  without an error, so either side can ship first.

### Frame to Desk

| Message | Fields | What the Desk does |
|---|---|---|
| `ready` | `version` | Reads it whatever number its envelope carries. Version 1: hides its own header (the frame has one), then answers with `ticket` or `ticket-error`, then `page`, then `visibility`. Any other version: the classic widget takes over at once. It comes again if the page in the frame reloads itself; the answer is the same. |
| `need-ticket` | `reason` | Asks the server for a new ticket and posts `ticket` or `ticket-error`. At most 6 a minute. |
| `signed-in` | none | Notes that the framed chat works for this person. From here nothing takes the panel away. |
| `sign-in-failed` | `reason` | Before the first `signed-in`: the classic widget takes over. After it: nothing, the frame is showing the reason and its own "Try again". |
| `open-record` | `path` | Checks the path, then hands it to the Desk's own router. See below. |
| `resize` | `mode` | `wide`: the panel becomes `min(1120px, window - 40px)` wide and full height. `panel`: back to normal. Nothing changes on a phone. |
| `close` | none | Hides the panel. The frame stays alive. |
| `key` | `key` | `alt+t`: opens or closes the panel, as Alt+T on the Desk does. `escape`: closes it. |
| `reply-finished` | `chat_id`, `title` | When the panel is closed or the tab is in the background: a line in the Desk's alert corner and a dot on the button. When the person is looking at the reply: nothing. No browser notification, so no permission prompt. |
| `clock-in` | `params` | Draws the Desk's own confirm card. See below. |

**`open-record`.** The person followed a link in a reply to a page of this Desk. The frame sends
the link's path, query and fragment; it has a URL and nothing else. The Desk follows it only when
it is a path under `/desk` or `/app`. It refuses a full URL, anything with a scheme
(`javascript:`, `data:`), a protocol-relative value (`//host/...`) and its backslash spelling, a
path outside the Desk, and one that only gets outside after `..` is resolved. An accepted path
goes to `frappe.set_route`, so it is one ordinary history entry and Back returns to where the
person was. If the router cannot take it, it opens in a new tab. On a phone the sheet closes so
the page is visible. This is the same rule the classic widget has always had for a `/app/...`
link in a reply.

**`clock-in`.** Clocking in needs the ERPNext session and a location fix from the device, and the
frame is allowed neither. So the message only asks. The Desk draws a card on its own page
("Clock in to PROJ-0002?", the project's name, Confirm, Cancel) and expects nothing more from the
frame.

- The message never clocks anyone in. Only the person pressing Confirm does.
- Location is asked for inside the Confirm click, fresh and precise, and goes with the call to
  the time kiosk (`api.time_kiosk.log_time`). No fix means no clock-in, and the card says so.
- Only five keys are read from `params`: `project`, `project_title`, `task`, `time_category`,
  `description`. A location in the message is ignored. With no `project` no card is drawn.
- The card names the project by its **id**, which is what the time kiosk is sent, and takes the
  project's name from the Desk (one read of `Project.project_name`, with the person's own
  permissions). The title in the message is shown only until that answer arrives, and stays only
  when the Desk cannot give one. A title a model wrote cannot dress up which project Confirm
  clocks into.

### Desk to frame

| Message | Fields | When the Desk sends it |
|---|---|---|
| `ticket` | `ticket` | In answer to `ready` or `need-ticket`. Posted at once and not kept. |
| `ticket-error` | `message` | When there is no ticket to give: one plain sentence of at most 300 characters. The Desk does not also draw a message of its own over the frame. |
| `page` | `page` | After the first `ticket`, on every Desk route change, and, while the panel is open, within about two seconds of a list's filters or a form's unsaved state changing. Only when it differs from the last one sent. `null` when there is nothing to offer, and once as `null` when *Enable "Add page to context"* is off. |
| `ask` | `prompt`, `page` | `SapphireTriton.ask(prompt, context)`: the panel opens and the prompt goes into the frame's message box, at most 8,000 characters. `page` is the context, and is left out when there is none. The Desk never sets `send`; the person reads the brief and sends it. |
| `visibility` | `open` | After the first `ticket`, and whenever the panel opens or closes. The frame puts the cursor in its message box on `open: true`. |

**The page reference** is what `detectPageContext()` in `public/js/capture/page_context.js`
returns, passed through: a record, a list or a report with its filters, and `unsaved: true` when
the form has unsaved edits. It never carries field values. One thing is taken out on the way: a
list's `filters` when they belong to a different list than the one the route names (`cur_list`
outlives its page for a moment). The frame only offers the page as a chip; nothing goes to the
assistant unless the person taps it.

## When the classic widget takes over

A person is never left with a dead panel. The framed widget is removed whole, its listeners
included, and the classic one is started in its place, open if the panel was open and with any
waiting `ask()` prompt carried over. It happens once per page load; one line goes to the console
and nothing is shown.

| What happened | How long the Desk waits | Console reason |
|---|---|---|
| The loader's two files did not arrive | 10 seconds | `loader` |
| The frame never said `ready` | 20 seconds from creating it. The panel says "Connecting to Triton…" meanwhile | `timeout` |
| The frame finished loading and still said nothing (the browser refused it, or the page is not Triton's chat) | 8 seconds after its `load` event | `silent` |
| `ready` in a contract version other than 1 | at once | `contract` |
| Triton has no ticket route | at once | `no-ticket-route` |
| `sign-in-failed` before the frame ever signed in | at once | `sign-in-failed, <reason>` |
| Nothing about sign-in after `ready` | 20 seconds | `no-sign-in` |

Once the frame has said `signed-in`, none of these applies again on that page load. A token that
cannot be renewed is the frame's to show, with its own "Try again"; the conversation is on
Triton's server and comes back after a successful retry.

The classic widget needed two small edits for this, both in its bootstrap: it stays asleep while
boot carries `triton_frame`, and it exports `SapphireTriton.startClassic()` for the hand-back.

A first sign-in that fails for a passing reason (Triton briefly unreachable, the per-minute limit)
also ends on the classic widget for that page load, although Triton's contract would let the
frame show "Try again". That is deliberate: the classic widget is the one that is known to work.

The frame's stylesheet stays loaded after a hand-back. Whatever it says about the button's own
classes is scoped to a class on `<html>` (`triton-framed`) that the loader removes as it goes, so
the classic widget is not restyled by it.

## What the framed widget does not do yet

Things a pilot person will notice, none of them the loader's to fix:

- **No morning briefing on the first open of the day.** The classic widget shows it; the framed
  chat does not yet.
- **The frame does not follow the Desk's light or dark theme.** It follows the person's Triton
  theme setting.
- **Attached files go to Triton with the message**, as on the Triton site. The attachment switch,
  size cap and 30-day cleanup in *Triton Assistant Settings* apply to the classic widget only.
- **Connecting Google, the profile and memory** open the Triton site in a new tab.

## Where each piece lives

| Piece | File |
|---|---|
| Who gets which widget, the boot payload, the ticket method | `erpnext_enhancements/api/triton_frame.py` |
| The boot key | `erpnext_enhancements/boot.py` (`_triton_frame`) |
| The settings fields | `ai_governance/doctype/triton_assistant_settings/` |
| The `widget_mode` backfill | `patches/backfill_triton_widget_mode.py` |
| The gate (main bundle) | `public/js/global_enhancements/triton_frame_gate.js` |
| The loader bundle | `public/js/triton_frame.bundle.js`, `public/css/triton_frame.bundle.css` |
| Button, panel, frame, fallback | `public/js/triton_frame/loader.js` |
| Origin and source checks | `public/js/triton_frame/bridge.js` |
| The contract's version and message names | `public/js/triton_frame/contract.js` |
| The page reference | `public/js/triton_frame/page_source.js`, `public/js/capture/page_context.js` |
| The link check | `public/js/triton_frame/record_link.js` |
| The clock-in card | `public/js/triton_frame/clock_in.js` |

## Tests

Both suites are bench-free and run in CI.

```bash
node scripts/test_triton_frame.mjs
```

```bash
python -m pytest erpnext_enhancements/tests/test_triton_frame.py -q
```

The node suite drives the real loader, gate and bridge against a stand-in window. It holds the
version and the message names in `contract.js` to this page. Where Triton's own
`docs/embed-bridge.md` can be found (a Triton checkout beside this repository, or a path in
`TRITON_EMBED_BRIDGE_DOC`) it holds them to that document too; on CI there is no Triton checkout,
so that one check is skipped there.
