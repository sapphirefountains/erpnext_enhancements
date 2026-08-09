# 0009 Appendix A. Behaviour inventory of the existing floating Triton chat widget

- **Status:** Proposed — appendix to `decisions/adr/0009-erpnext-google-chat-triton.md`
- **Date:** 2026-08-07
- **Type:** Phase 3 **gate artifact**

## What this document is

Locked decision #8 says the chat frontend **reuses and extends the existing floating widget**;
the bubble stays the entry point. Nothing about that widget may be changed until its current
behaviour is written down. This is that writing-down. **Phase 3 (`PHASE_3_chat_spa`) is
instructed to STOP if this file does not exist**, and to re-check §14's table row by row as its
regression suite.

Three consequences of that, stated plainly:

1. **This describes behaviour as of Phase 0, not desired behaviour.** Several rows below record
   things that are wrong (§13). They are recorded, not fixed — Phase 0 writes no code.
2. **A divergence found in Phase 3 between this document and the running widget is a regression
   to report, not a spec to update.** If Phase 3 finds that the widget no longer does what §1–§12
   say, the correct action is to raise it against whatever changed it — not to edit this file to
   match. This file is immutable in the same sense the ADRs are (`decisions/adr/README.md`).
3. **Where a behaviour here is deliberately being replaced**, the replacement must be named in
   Appendix B and the row in §14 marked as *intentionally changed* in the Phase 3 PR description.
   Silence is treated as a regression.

## The exact state this describes

| Repo | Path | Commit / state |
|---|---|---|
| `erpnext_enhancements` | worktree `.claude/worktrees/sf-google-chat-ref-86390f`, branch `claude/sf-google-chat-ref-86390f` | `6da57b85`, working tree clean; `__version__ = "1.260.3"` (`erpnext_enhancements/__init__.py:1`) — **as of the commit this inventory describes.** The ADR that carries this appendix bumps the version to `1.260.4` in the same PR, so a Phase 3 session comparing against a later worktree should expect that difference and not read it as drift |
| `triton` (cited as `triton:`) | `C:/Users/nbbsh/Documents/GitHub/triton` | `919c3b9c`, branch `main` |
| `frappe` (cited as `frappe:`) | local checkout `C:/Users/nbbsh/Documents/GitHub/frappe`, read for framework primitives the widget calls | `origin/version-16` @ `9523516cac25992bc2cd810e1015df8994c257f5` (tag `v16.30.0`) — **but that checkout's working tree sits on `develop` @ `5da68e856c`.** Every `frappe:` line number below has been re-resolved against `origin/version-16` and must be re-checked the same way — `git show origin/version-16:<path>`, or a real v16 bench. Reading those files as they sit on disk yields `develop` numbering, which differs by 20–60 lines in `request.js`, `utils.js` and the desk SCSS. |

**The widget is three files, and one file that is not it:**

| File | Lines | Role |
|---|---|---|
| `erpnext_enhancements/public/js/global_enhancements/triton_widget.js` | 1404 | one anonymous IIFE (`:15` opens, `:1404` closes). All widget behaviour. **Zero exports; nothing in the repo imports a symbol from it** — a refactor cannot break a caller, because there are none. Verified: the only non-CHANGELOG references anywhere are the bundle import (`public/js/erpnext_enhancements.bundle.js:80`), the SCSS import (`public/css/desk_addons.bundle.scss:23`) and two README lines (`public/README.md:69`, `:152`). |
| `erpnext_enhancements/public/css/global_enhancements/triton_widget.css` | 759 | all styling |
| `erpnext_enhancements/triton_chat.py` | 601 | the server-side proxy |

**Not the widget:** `erpnext_enhancements/api/briefing.py` and `tests/test_briefing.py` are
ERPNext's *own* Daily Briefing feature. The widget's morning briefing is a different thing that
proxies to Triton (`triton_chat.py:490`). Do not conflate them.

### Where this record lives

The Phase 0 prompt asks for `docs/adr/0001-appendix-a-widget-behavior-inventory.md`. This repo
already has an ADR convention (`decisions/adr/README.md`: records at `decisions/adr/NNNN-slug.md`,
sequential, immutable once accepted, indexed in the README), and `0001` is taken by
`0001-record-architecture-decisions.md`. The prompt's path would collide on the number and start
a rival ADR namespace. Per canonical decision D0 the appendix lives here instead. A human
checking the prompt's literal path is not looking at a miss.

---

## 1. Entry point — injection, pages, conditions, roles, mount, z-index, coexistence

### 1.1 How the code reaches the browser

- **Content-hashed esbuild bundle, never a raw `/assets` path.** `hooks.py:43-59` declares
  `app_include_js`. The widget is not listed individually — it arrives via
  `"erpnext_enhancements.bundle.js"` (`hooks.py:58`), which imports it at
  `public/js/erpnext_enhancements.bundle.js:80`. The rationale is recorded twice
  (`hooks.py:30-35`, `erpnext_enhancements.bundle.js:1-27`) and is ADR 0008: raw `/assets` paths
  are served immutable for a year with no content hash, so an edit never reaches an
  already-cached device.
- **CSS the same way.** `hooks.py:36-42` lists `"desk_addons.bundle.css"`, which is the SCSS at
  `public/css/desk_addons.bundle.scss`. Its **first** import (line 23) is
  `./global_enhancements/triton_widget`. Being first is load-bearing: the file's own header
  (`desk_addons.bundle.scss:7-9`) states the order deliberately mirrors the old
  `app_include_css` order. The `.scss` (not `.css`) entry is also load-bearing —
  `desk_addons.bundle.scss:11-21` records that a plain `.css` entry breaks the Frappe Cloud
  deploy with ENOENT because postcss2 relocates the file before esbuild resolves relative
  `@import`s.
- **Load order inside the bundle matters.** `erpnext_enhancements.bundle.js:79` imports
  `mermaid_theme.js` immediately before `:80` imports `triton_widget.js`; the comment at `:77-78`
  explains `window.sf_mermaid` must exist before its consumers. The widget reads it at
  `triton_widget.js:79`.
- **`esc` is bound once at IIFE evaluation:** `const esc = frappe.utils.escape_html;`
  (`triton_widget.js:44`). If `frappe.utils` were undefined when the bundle executes, the whole
  IIFE throws and the widget never registers its `app_ready` handler. **Any bundle-order change
  must keep the widget after frappe's own bundles.**

### 1.2 Which pages

Every ERPNext **desk** page, unconditionally on route. The mount appends to `document.body`
(`triton_widget.js:218`, `:255`); the desk is a SPA so the widget survives every in-app route
change without re-running. **Website/portal pages do not get it** — the entry is only in
`app_include_js`, never `web_include_js`.

Build-once guard: `triton_widget.js:1383` `let _booted = false;` and `:1385` `if (_booted) return;`
("Desk is a SPA; build the widget exactly once").

Two bootstrap triggers, belt-and-braces: `$(document).on("app_ready", init)`
(`triton_widget.js:1401`) and `$(() => setTimeout(init, 1500))` (`:1403`), the fallback existing
because `app_ready` may have fired before the script ran (`:1402`). `:1392` resets
`_booted = false` when `get_config` throws, so the 1.5 s fallback can retry.

### 1.3 The three gates — and there is no Frappe *role* gate

| Gate | Where | Rule |
|---|---|---|
| Session | `triton_widget.js:1386` | returns early unless `window.frappe && frappe.xcall && frappe.session && frappe.session.user !== "Guest"` |
| Server config | `triton_widget.js:1390`, `:1395` | `cfg = await xcall("get_config")`; `if (!cfg || !cfg.enabled) return;` — **nothing is injected into the DOM before this resolves. There is no DOM at all for a disabled user, not a hidden node.** |
| Access | `triton_chat.py:89-104` | `user_has_widget_access()`: `True` for everyone when `restrict_to_whitelist` is off (`:99-100`); otherwise `True` only for `Administrator` (`:102-103`) or a user in the `allowed_users` child table (`:104`) |

`get_config` folds the access gate into `enabled`
(`triton_chat.py:253`: `"enabled": s["enabled"] and user_has_widget_access(s)`, rationale `:249-251`).

**Server-side enforcement is real, not cosmetic:** every Triton call mints a token through
`mint_user_token()`, which re-checks the whitelist and throws `frappe.PermissionError`
(`triton_chat.py:121-122`, rationale `:118-120`). Calling the whitelisted methods directly does
not bypass it.

There is **no** `frappe.only_for(...)`, no role check and no `has_permission` anywhere in
`triton_chat.py`. Membership of a Role is irrelevant to the widget today.

### 1.4 Settings that change widget behaviour

All on the `Triton Assistant Settings` Single
(`erpnext_enhancements/ai_governance/doctype/triton_assistant_settings/triton_assistant_settings.json`).
Only `System Manager` has any permission on the doctype (`:105-115`).

| Field | JSON | Default | Effect |
|---|---|---|---|
| `enabled` | `:32-37` | `1` | master switch; widget never builds when off |
| `default_model` | `:38-43` | blank | falls back to `Triton Settings.chat_model_id`, then literal `"gemini-3.5-flash"` (`triton_chat.py:34`, `:74`) |
| `request_timeout` | `:44-50` | `120` | seconds; read timeout on every proxy call including the stream (`triton_chat.py:75`, `:580`) |
| `enable_page_context` | `:55-61` | `1` | hides the "＋ Add this page" button (`triton_widget.js:306-308`) and short-circuits `suggestCurrentPage` (`:825`) |
| `enable_write_actions` | `:62-68` | `1` | returned by `get_config` (`triton_chat.py:255`) and **never read by the widget** — see §13 D-3 |
| `debug_logging` | `:69-75` | `0` | gates the three `frappe.log_error` calls (`triton_chat.py:147`, `:182`, `:585`) |
| `restrict_to_whitelist` | `:81-87` | `0` | turns the whitelist on |
| `allowed_users` | `:88-95` | — | child table of `Triton Allowed User` |

**Connection settings live in a different, shared Single** — `Triton Settings`, read at
`triton_chat.py:62-65`: `gateway_url`, `admin_webhook_secret` (via `get_password`),
`chat_model_id` (field names confirmed in `triton_settings.json:33`, `:145`, `:78`). A missing
`Triton Settings` doctype is swallowed (`triton_chat.py:66-68`) and the assistant stays off.

### 1.5 DOM mount, z-index, coexistence

- **Mount point:** two siblings appended directly to `document.body` —
  `<button class="triton-fab">` (`triton_widget.js:213-218`) and `<div class="triton-panel">`
  (`:220-255`). Not inside `#body`, not inside the page container, **not in a shadow root, not
  an iframe**. Consequence for any refactor: the widget shares the desk's global CSS cascade and
  the global `document` keydown stream. **There is no style isolation today.**
- **z-index:** `.triton-fab` = `1040` (`triton_widget.css:27`), `.triton-panel` = `1041`
  (`triton_widget.css:60`). The history/personas overlay is `position:absolute; inset:0;
  z-index:5` *within* the panel (`triton_widget.css:485-488`).
- Desk neighbours, resolved on `version-16`: sidebar `1020–1023`
  (`frappe:frappe/public/scss/desk/sidebar.scss:50`, `:253`, `:302`, `:325`); menu `1030`
  (`frappe:frappe/public/scss/desk/menu.scss:15`); global-search dropdown `1070`
  (`frappe:frappe/public/scss/desk/global_search.scss:137`); form-sidebar overlays `1100` and
  `1300` (`frappe:frappe/public/scss/desk/form_sidebar.scss:393`, `:363`); datepicker inside a
  dialog `1140` (`frappe:frappe/public/scss/common/modal.scss:144-146`). There is **no
  `workspace_dock.scss` on `version-16`** — that file exists only on `develop`, so the dock is not
  part of the v16 z-index neighbourhood at all.
- So the widget deliberately sits **above** the desk sidebar and menu and **below** dialogs —
  which is required, because the persona editor is a `frappe.ui.Dialog` (`triton_widget.js:537`)
  that must render over the panel. See VERIFY V-2 (§15) for the one unmeasured value.
- **Coexistence:** `position: fixed`, 24 px bottom-right inset (`triton_widget.css:14-16`); the
  panel anchors 24 px right / 88 px up (`:48-50`) so it clears the FAB. **Nothing reserves layout
  space; the desk never reflows around it.** It simply overlaps whatever is bottom-right of the
  viewport.

---

## 2. The full state machine

**Headline: there is no minimize, no fullscreen, no drag and no resize.** Confirmed by
`rg -ni "drag|resize|minimi|fullscreen|maximi" triton_widget.js` → zero hits outside
`resize: none` on the textarea (`triton_widget.css:429`). Any "minimized / dragging / resized"
state a downstream phase assumes is **new** behaviour, not preserved behaviour.

### 2.1 States

| # | State | Representation | Set at |
|---|---|---|---|
| S1 | Panel closed (default) | `state.open === false`; `.triton-panel` lacks `.triton-visible`; `opacity:0; visibility:hidden; pointer-events:none` (`triton_widget.css:68-70`, `:75`) | `triton_widget.js:36` initial, `:748` |
| S2 | Panel open | `.triton-visible` on panel (`:748`), `.triton-fab-open` on FAB (`:749`) | `:746-762` |
| S3 | History overlay open | `.triton-history-open` on `.triton-history-panel` (`:628`) | `openHistory()` `:627-630` |
| S4 | Personas overlay open | `.triton-history-open` on `.triton-personas-panel` (`:460`) | `openPersonas()` `:459-462` |
| S5 | Streaming | `state.streaming === true` (`:1242`), send button disabled (`:1243`), `.triton-streaming` on the live wrap (`:993`) | `send()` `:1239-1266` |
| S6 | Thinking disclosure live | `<details class="triton-thinking">` open, shimmer label, 500 ms timer (`:1023-1047`) | `ensureThinking()` |
| S7 | Thinking settled | `.triton-thinking-done`, `details.open = false`, label "Thought for Ns" (`:1065-1083`) | `collapseThinking()` |
| S8 | Empty | `.triton-empty` placeholder (`:850-857`) | `showEmpty()` |
| S9 | Briefing | `.triton-briefing` on the assistant wrap (`:725`) | `startDailyBriefing()` `:715-743` |
| S10 | Reduced motion | module-level const captured **once** at IIFE eval (`:62-63`) | never re-evaluated |

**S3 and S4 are independent** — two separate elements both keyed on `.triton-history-open` — so
**both overlays can be open at once**, and `toggle(false)` calls only `closeHistory()`
(`:760`), so a personas overlay left open stays open behind the next panel open. Reproducible
state leak; recorded as defect D-6 (§13).

### 2.2 Every transition

| From | Trigger | To | Code |
|---|---|---|---|
| S1 | click FAB | S2 | `:217` → `toggle()` `:746` |
| S1 | `Alt+T` / `Alt+Shift+T` anywhere on the page | S2 | `:299-304` |
| S2 | click FAB, click `.triton-close`, `Alt+T` | S1 | `:217`, `:281`, `:299` |
| → S2 | on every open: `suggestCurrentPage()` then `text.focus()` | — | `:751-752` |
| → S2 | first open of a local calendar day: `startDailyBriefing()` | S9 | `:753-755` |
| → S2 | else, if no `sessionId` **and** `state.messages_loaded !== true`: `loadHistory()` | S8 or rendered history | `:756-758` |
| → S1 | on close: `closeHistory()` only (**personas panel NOT closed**) | S3 cleared | `:760` |
| S2 | click 🕘 | S3 + `loadSessions()` | `:283`, `:627-630` |
| S3 | click ← | S2 | `:284`, `:632-634` |
| S3 | click a session row | S2, `selectSession(id)` | `:666`, `:670-694` |
| S3 | click a session row **while streaming** | no-op | `:671` `if (state.streaming) return;` |
| S2 | persona `<select>` → `__manage__` sentinel | S4; select value restored first | `:449-457` |
| S4 | click ← | S2 | `:287`, `:464-466` |
| S4 | click a persona label | S2, persona set | `:503-506` |
| S2 | click ✎ (header) `newChat()` | S8 + `triton-fresh` pulse | `:282`, `:1208-1218` |
| S2 | Enter in textarea (no Shift) | S5 | `:291-296` |
| S2 | click ➤ | S5 | `:290`, `:1231-1237` |
| S5 | `done` or `error` SSE frame | S2 | `:1357-1376` → `finishStreaming()` `:1003-1020` |
| S5 | thrown error anywhere in `send()` | S2, italic error appended | `:1259-1266` |
| S5 | first `text` event | S6→S7 + all active steps marked done | `:987-990` |
| S2 | approve an action card | hidden continuation → S5 | `:1144-1158`, `:1153` |

### 2.3 What persists, where, and for how long

**Only `localStorage`. No `sessionStorage`, no Frappe User Setting, no `extend_bootinfo` key, no
cookie.** Confirmed: `erpnext_enhancements/boot.py` sets twelve `ee_*` bootinfo keys (`boot.py:75-86`)
and **none is Triton-related**; `hooks.py:751` wires
`extend_bootinfo = "erpnext_enhancements.boot.boot_session"`.

Exact key names, quoted from `triton_widget.js:17-25`:

| Constant | **Literal key** | Value | Written | Read | Cleared |
|---|---|---|---|---|---|
| `LS_SESSION` | **`"triton_session_id"`** | Triton `ChatSession.id` as a string | `:673`, `:1227` | `:1162` (`parseInt(saved, 10)` at `:1168`) | `:690`, `:718`, `:1181`, `:1210` |
| `LS_MODEL` | **`"triton_model"`** | model id; `""` = auto | `:379` | `:342` | never |
| `LS_PERSONA` | **`"triton_persona_key"`** | persona key, e.g. `custom:42` | `:444` | `:423` | `:445` when persona cleared |
| `LS_BRIEF` | **`"triton_briefing_date"`** | local `YYYY-MM-DD` from `todayStr()` (`:697-706`) | `:716` | `:709` | `:741` on briefing failure |

- **Not persisted:** open/closed state, panel size or position, scroll position, draft text in the
  textarea, pinned context chips, which overlay was open. A full page reload always reopens
  closed, chip-less, with a blank textarea.
- **Across in-desk navigation:** everything survives, because the desk is a SPA and the widget is
  never rebuilt (`:1383-1385`). In-flight streams also survive route changes — §4.6.
- **Across sessions/devices:** the four keys are per-browser-profile, per-origin, and never
  expire. Conversation *content* is not in localStorage — only the session id. Messages live in
  Triton's `ChatMessage` table (`triton:backend/app/api/v1/endpoints/streaming.py:155-171`).
- **Cross-surface:** `LS_PERSONA` and the session are effectively shared with the Triton web app,
  because both are keyed on the same Triton user resolved by the identity bridge
  (`triton_widget.js:19-21`, `triton_chat.py:345-347`).
- **Stale-key self-healing:** a `LS_SESSION` pointing at a deleted Triton session is dropped on
  the failed `get_messages` (`:1180-1181`, `:690`). A `LS_PERSONA` pointing at a persona deleted
  in the Triton web app is **silently ignored rather than pinned** (`:419-429`, rationale
  `:420-421`) — it is not removed from storage, just not used.

---

## 3. The request path to Triton

### 3.1 Two transports, deliberately different

1. **Everything except the chat turn** goes through `frappe.xcall` — `triton_widget.js:45`:
   ``const xcall = (m, args) => frappe.xcall(`${METHOD}.${m}`, args);`` with
   `METHOD = "erpnext_enhancements.triton_chat"` (`:16`). `frappe.xcall` wraps `frappe.call`
   (`frappe:frappe/public/js/frappe/request.js:13-27`; `frappe.call` itself begins at `:31`),
   which defaults to `POST` to
   `/api/method/...` with `cmd` in the body; the framework attaches the CSRF token and session
   cookie.
2. **The chat turn** uses a raw `fetch`, because `frappe.xcall` cannot stream
   (`triton_widget.js:1269-1291`).

### 3.2 The streaming request, field by field

```
POST /api/method/erpnext_enhancements.triton_chat.stream_query      (triton_widget.js:1270)

Headers                                                            (:1272-1276)
  Content-Type:        application/json
  X-Frappe-CSRF-Token: frappe.csrf_token
  Accept:              text/event-stream

Credentials: default ("same-origin") — the `sid` cookie rides along; no `credentials:`
  option is set and none is needed for a same-origin fetch.

Body (JSON)                                                        (:1277-1286)
  session_id  : number   — state.sessionId, guaranteed non-null by ensureSession()
  prompt      : string   — the raw user text
  context     : string   — JSON.stringify(state.contextRefs); literally "[]" when hidden
  hidden      : 1 | 0    — a number, not a boolean
  model       : string   — state.model; "" = let Triton auto-route
  persona_key : string   — state.persona; "" = plain Triton voice
```

- **No `Authorization` header, and no Triton credential ever reaches the browser** — that is the
  entire point of the proxy (`triton_chat.py:5-6`).
- `context` is a **string containing JSON**, not a nested object: Frappe form-encodes whitelisted
  args and the server re-parses with `json.loads` (`triton_chat.py:204-206`).

### 3.3 The whitelisted proxy surface, with decorator arguments

Every one of these is a **bare `@frappe.whitelist()`** — **no `allow_guest`, no `methods=[...]`,
no `xss_safe`, and no `@rate_limit`** (unlike every guest endpoint elsewhere in the app).
Verified individually:

| Widget call | Python fn | Decorator line | Triton path |
|---|---|---|---|
| `get_config` | `triton_chat.py:243` | `:242` | — (local only) |
| `start_session` | `:266` | `:265` | `POST /api/v1/assistant/sessions` (`:283`) |
| `list_sessions` | `:287` | `:286` | `GET /api/v1/assistant/sessions` (`:289`) |
| `list_models` | `:318` | `:317` | `GET /api/v1/assistant/models` (`:331`) |
| `list_personas` | `:388` | `:387` | `GET /api/v1/personas` (`:403`) |
| `create_persona` | `:413` | `:412` | `POST /api/v1/personas` (`:421`) |
| `update_persona` | `:433` | `:432` | `PUT /api/v1/personas/custom/{int}` (`:454`, path built `:371-384`) |
| `delete_persona` | `:460` | `:459` | `DELETE /api/v1/personas/custom/{int}` (`:462`) |
| `duplicate_persona` | `:468` | `:467` | `POST /api/v1/personas/duplicate` (`:474`) |
| `set_default_persona` | `:480` | `:479` | `PUT /api/v1/personas/selection` (`:482`) — **never called by the widget** |
| `morning_briefing` | `:490` | `:489` | `GET /api/v1/assistant/morning-briefing?force=&cached_only=` (`:497-501`) |
| `get_messages` | `:505` | `:504` | `GET /api/v1/assistant/sessions/{int}/messages?limit={int}` (`:507-509`) |
| `delete_session` | `:514` | `:513` | `DELETE /api/v1/assistant/sessions/{int}` (`:516`) — **never called by the widget** |
| `confirm_action` | `:520` | `:519` | `POST /api/v1/integrations/actions/{id}/confirm` (`:522`) |
| `cancel_action` | `:528` | `:527` | `POST /api/v1/integrations/actions/{id}/cancel` (`:530`) |
| `stream_query` | `:540` | `:539` | `POST /api/v1/assistant/sessions/{int}/query/stream` (`:567`) |

**Permission model.** A bare `@frappe.whitelist()` means "any authenticated user". The real
authorization is the whitelist gate inside `mint_user_token` (`triton_chat.py:112-122`), which
every path except `get_config` reaches. `get_config` is deliberately safe to call unauthorized:
it returns `{"enabled": False}` on any exception (`:246-248`) and never returns the gateway
secret (`:244`).

**Injection hardening worth preserving.** `_custom_persona_path` (`triton_chat.py:371-384`)
refuses anything non-integer rather than interpolating into a URL path; the persona *key* (which
contains a colon) travels in the body instead (`:474`, and `triton:backend/app/schemas/persona.py:73-78`
documents the same reason Triton-side). Every other id goes through `cint()` (`:507`, `:516`,
`:523`, `:531`, `:567`).

**`_request` retry.** One silent retry on HTTP 401 with a force-refreshed token
(`triton_chat.py:177-179`). Any other `>= 400` throws a generic `_("Triton error ({0}).")`
(`:184`) — **the upstream body is never shown to the user**; it reaches the Error Log only when
`debug_logging` is on (`:182-183`).

### 3.4 Auth — the identity bridge

- `mint_user_token()` (`triton_chat.py:112-155`) does
  `POST {base_url}/api/v1/auth/erpnext-bridge/token` with
  `Authorization: Bearer {admin_webhook_secret}` (`:140`) and body
  `{"email": <User.email or session.user>, "full_name": frappe.utils.get_fullname(user)}`
  (`:139`, `_user_email()` at `:107-109`). Timeout hard-coded **15 s** (`:141`).
- Response consumed as `data["access_token"]` and `data.get("expires_in", 1800)` (`:152-153`),
  cached in `frappe.cache()` under **`f"triton_user_token::{user}"`** (`:124`) for
  `max(ttl - 120, 60)` seconds (`:154`, margin constant `_TOKEN_REFRESH_MARGIN_SEC` at `:31`).
- Triton side confirms the contract at
  `triton:backend/app/api/v1/endpoints/erpnext_bridge.py:88-134`. The secret is compared with
  `hmac.compare_digest` (`:84`); a missing server secret → 503 (`:76-80`); mismatch → 401 (`:85`).
  Email is lower-cased (`:96`) and **the domain is hard-gated to `sapphirefountains.com`**
  (`:56`, `:100-106`) — a non-domain ERPNext user gets a 403 that surfaces in the widget as the
  generic "Triton authentication failed (403)." Unknown in-domain emails are **auto-provisioned**
  as Triton users (`:108-121`). TTL is `30 * 60` s (`:52`), i.e. the `expires_in: 1800` the proxy
  caches against.
- **Two caches, scoped differently on purpose.** `triton_models_list` is one **site-wide** key
  (`triton_chat.py:326`, 300 s TTL `:338`); personas use **`f"triton_personas::{frappe.session.user}"`**
  (`:356`, 60 s TTL `:408`). The rationale is spelled out at `:348-355` — a shared persona key
  would serve one user's private personas to the whole site. **This is the single
  highest-severity correctness property in the proxy** and it is the one thing already covered by
  a test (§14 note on `tests/test_triton_personas.py`).

---

## 4. Streaming — the part that must survive

### 4.1 Protocol, by name: SSE over chunked HTTP, relayed byte-for-byte

Not WebSocket, not polling, not long-poll. Triton emits SSE
(`triton:backend/app/api/v1/endpoints/streaming.py:474-482`, `media_type="text/event-stream"`) and
the ERPNext proxy re-emits it unchanged.

`stream_query` returns a **werkzeug `Response`**, bypassing Frappe's JSON response pipeline
entirely (`triton_chat.py:597-601`):

```python
return Response(
    generate(),
    mimetype="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

`X-Accel-Buffering: no` disables nginx response buffering — without it the whole stream arrives
at once and the typewriter UX dies silently.

The relay is `for chunk in r.iter_content(chunk_size=None): yield chunk`
(`triton_chat.py:591-593`) — **no re-framing, no parsing, no re-encoding.** Frame boundaries are
whatever TCP delivers.

**The structural constraint most likely to be broken by a well-intentioned refactor**, documented
at `triton_chat.py:545-547`: everything the generator needs (`token`, `base_url`, `timeout`,
`debug`, `payload`, `url`) is captured into locals at `:553-567` *before* the `Response` is
returned, because the lazy body runs after Frappe has torn down the request/DB context. **Inside
`generate()` there is no `frappe.session`, no usable `frappe.db` and no site context.** A refactor
that wants to persist streamed chunks into a DocType cannot do it inside this generator; the
options are buffer-and-write-once after the stream closes, write from a job enqueued before the
Response is returned, or re-establish site context inside the generator (a substantially larger
change). Such a refactor **fails in production and passes in unit tests**.

### 4.2 Frame parsing in the browser

`triton_widget.js:1293-1307` — a hand-rolled SSE reader, not `EventSource` (`EventSource` cannot
POST, which is why):

```js
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  let idx;
  while ((idx = buffer.indexOf("\n\n")) >= 0) {
    const frame = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    handleFrame(frame, live);
  }
}
```

`handleFrame` (`:1309-1322`) keeps only lines starting with `data:`, slices 5 chars and `.trim()`s
each (`:1312-1313`), joins multi-line data with `\n`, and `JSON.parse`s. **A parse failure is
silently swallowed** (`:1318-1320`).

Properties a refactor must not lose:

- SSE `event:` / `id:` / `retry:` fields are ignored. Triton uses none of them —
  `triton:.../streaming.py:62-64`, `_sse()` emits only `data: `.
- The `: ping\n\n` comment Triton sends first (`triton:.../streaming.py:326`, to flush headers
  through proxies and drive TTFB toward zero) produces zero `data:` lines and is correctly
  ignored by `:1314`.
- `.trim()` on each data line means **leading/trailing whitespace inside a payload line is
  destroyed**. Harmless for JSON; fatal for any future raw-text protocol.
- The buffer is never bounded and never flushed at `done`, so a final frame not terminated by
  `\n\n` is dropped.
- `getReader()` requires `res.body`; the widget explicitly throws when it is absent (`:1289`),
  which is the guard against a browser or proxy that buffered the whole response.

### 4.3 Event vocabulary — every `ev.type` the widget handles

`handleEvent` switch, `triton_widget.js:1324-1380`:

| `ev.type` | Fields read | Behaviour | Line |
|---|---|---|---|
| `tool_status` | `ev.content` | `clearStatus`, `pushStep` | `:1326-1329` |
| `agent_spawn` | `ev.label` ‖ `ev.agent` | `clearStatus`, `pushStep(label + " working…")` | `:1330-1333` |
| `thought` | `ev.content` | `clearStatus`, `appendThought` | `:1334-1337` |
| `text` | `ev.content` | `clearStatus`, `appendText` | `:1338-1341` |
| `sources` | `ev.content` | `renderSources(live.wrap, ev.content)` | `:1342-1344` |
| `pending_action` | `ev.params` | `renderActionCard(..., {liveStatus:"pending"})` | `:1345-1347` |
| `ui_command` | `ev.command`, `ev.params` | `render_chart` → inline chart; `render_visualization` / `render_3d_simulation` → pointer note; everything else ignored | `:1348-1356` |
| `done` | `ev.content`, `ev.ui_metadata.sources`, `ev.ui_metadata.direct_chart` | recover text if empty, render sources/chart if not already present, `finishStreaming` | `:1357-1371` |
| `error` | `ev.content` | append `\n\n*{escaped}*`, `finishStreaming` | `:1372-1376` |
| anything else | — | **silently dropped** (`default: break;`) | `:1377-1378` |

Cross-checked against what Triton emits (`triton:.../streaming.py:96-196`): `thought`, `text`,
`tool_status`, `pending_action`, `ui_command`, `visualization`, `sources`, `done`, `error`.

**Two mismatches, both real:**

1. Triton's `visualization` event (`triton:.../streaming.py:125-129`) hits the widget's
   `default:` branch and is **silently dropped**. Only `ui_command`-wrapped visualizations render
   a pointer note.
2. **No Triton code path emits a standalone `sources` event today.** The only occurrences of
   `"type": "sources"` across `triton:backend/` are the relay branch
   (`triton:.../streaming.py:131-133`) and `sources` as a *key on the `done` event*
   (`triton:backend/app/core/reasoning_engine.py:254`,
   `triton:backend/app/core/intelligence.py:1199-1203`, `:1301-1305`,
   `triton:backend/app/core/orchestrator.py:221-225`,
   `triton:backend/app/core/deep_research.py:327`, `:485`). So `triton_widget.js:1342-1344` is
   **defensive dead code today**; the live path is the `done` branch reading
   `ev.ui_metadata.sources` (`:1363`). `ui_metadata` is populated by the consumer at
   `triton:.../streaming.py:143-153` and stamped onto the outgoing `done` event at `:182-184`.

### 4.4 How partial tokens reach the DOM — the typewriter pump

This is subtler than "append the chunk" and is the easiest thing in the widget to lose in a
rewrite.

- `appendText` (`triton_widget.js:984-1000`): on the first text of a turn it settles the reasoning
  UI — `collapseThinking(live)` + `markActiveStepsDone(live)` (`:987-990`) — then appends to
  `live.text` (`:991`).
- If streaming **and** reduced motion is off: add `.triton-streaming` and schedule the pump
  (`:992-994`). Otherwise reveal everything immediately and run mermaid (`:995-999`).
- `pumpText` (`:973-982`) runs on `requestAnimationFrame` and is **backlog-adaptive**:

  ```js
  const step = Math.max(2, Math.min(60, Math.ceil(remaining / 3)));
  ```

  2–60 characters per frame, about a third of the backlog — so bursty SSE chunks still read as
  smooth typing.
- `renderBubble` (`:960-963`) re-renders the **entire revealed prefix** through `md()` every
  frame: `live.bubble.innerHTML = md(live.text.slice(0, live.shownLen));` — a full markdown
  re-parse plus full DOM replacement, up to 60 fps. **Two consequences a refactor must handle:**
  (a) any DOM state inside the bubble — a selection, an expanded `<details>`, an event listener —
  is destroyed every frame; (b) **inline citation links added in a later phase will be recreated
  every frame while streaming**, so they cannot hold state and any click handler must be delegated
  from an ancestor.
- `scrollDown()` (`:55-58`) is called after every render — a hard `scrollTop = scrollHeight` with
  **no "the user scrolled up, don't yank them back" guard**. It is called from ten places:
  `:119`, `:175`, `:208`, `:686`, `:897`, `:910`, `:956`, `:962`, `:1058`, `:1134`.
- Thoughts use a separate rAF coalescer, one markdown re-render per frame (`:1049-1061`).
- The blinking caret is pure CSS: `.triton-streaming .triton-bubble::after { content: "▍" }` with
  `@keyframes tritonCursor` (`triton_widget.css:586-596`).
- `finishStreaming` (`:1003-1020`) cancels both rAFs, flushes `shownLen = text.length`,
  re-renders thoughts, collapses thinking, marks steps done, re-renders the bubble, runs mermaid,
  drops `.triton-streaming`, sets `live.streaming = false`.
- `renderMermaidIn` is called **only on completed messages**, never during the pump (`:998`,
  `:1017`), so a diagram cannot flicker mid-stream.
- **Reduced motion** is captured once at `triton_widget.js:62-63`
  (`matchMedia("(prefers-reduced-motion: reduce)").matches`). When on, the pump is bypassed
  entirely and text snaps in (`:995-999`); CSS also disables the cursor, shimmer and spin
  (`triton_widget.css:690-701`) and the panel spring (`:85-97`). **It is not a live listener** —
  changing the OS setting mid-session has no effect until reload.

### 4.5 Mid-stream error handling

Four paths, three of which land in the same visual treatment:

1. **Triton-side SSE `error` event** → `triton_widget.js:1372-1376`:
   `live.text += "\n\n*" + esc(ev.content || "Error") + "*"`, then `finishStreaming`. Triton emits
   it from `triton:.../streaming.py:460-472` ("Communication disruption detected.", also persisted
   as an assistant `ChatMessage` at `:463-471`) and from
   `triton:backend/app/core/reasoning_engine.py:204`, `:246`.
2. **Proxy-side failure** → `triton_chat.py:535-536` `_sse_error()` synthesises a well-formed
   `{"type": "error", "content": ...}` frame. Two triggers: upstream non-200 (`:582-590`, message
   `_("Triton returned {0}.")`) and any exception around the request (`:594-595`,
   `_("Connection error: {0}")`). Because it is a valid SSE frame the widget renders it
   identically to a Triton error. **A transport failure and a Triton 500 both arrive as
   normal-looking frames, not as an HTTP error.**
3. **Browser-side throw** (`fetch` rejects, `!res.ok`, `!res.body`) → thrown at `:1289-1291` as
   `new Error("HTTP " + res.status)`, caught by `send()` (`:1259-1262`): `clearStatus`, append
   `\n\n*Error: {escaped}*`, `finishStreaming`.
4. **The `finally` always restores interactivity** (`:1263-1266`): `state.streaming = false`, send
   button re-enabled. **There is no path that permanently locks the composer.**

**Double-escaping, recorded not fixed:** `esc()` is applied to error text that is then fed through
`md()` via `renderBubble`. `frappe.utils.escape_html`
(`frappe:frappe/public/js/frappe/utils/utils.js:231-244`) maps `& < > " ' \` =`, so an error
message containing `"` renders literally as `&quot;`. A rewrite that drops the `esc()` is
arguably *more* correct — but only because `md()` sanitizes (§6). Decide deliberately; see
defect D-4.

### 4.6 Cancellation, abort, timeouts

- **There is NO `AbortController` anywhere in the widget.** `rg -ni "abort" triton_widget.js` →
  zero hits. There is **no stop button**. The only in-flight affordances are the disabled send
  button (`:1243`) and the `if (state.streaming) return;` guards at `:1241` and `:671`.
- **Closing the panel does not abort the stream.** `toggle(false)` only flips classes (`:748-749`)
  and calls `closeHistory()` (`:760`). The `fetch` keeps running, the pump keeps writing into a
  hidden DOM node, and the message is fully rendered when the panel is reopened.
- **Navigating within the desk does not abort it either** — SPA route change, no unload. A hard
  reload or tab close aborts it browser-side, but **the server-side turn keeps going** and Triton
  still persists the assistant message (`triton:.../streaming.py:155-171`), so the answer
  reappears in history.
- **Client timeout: none.** No `setTimeout` guards the fetch; the only `setTimeout` in the file is
  the 1500 ms boot fallback (`:1403`).
- **Server timeouts:** `timeout=(15, timeout)` on the streaming POST (`triton_chat.py:580`) —
  **15 s connect, `request_timeout` s (default 120) read**. The read timeout applies *between
  chunks*, not to total duration, so a long turn that keeps emitting tokens never trips it, but a
  >120 s silent gap (a slow tool, say) kills the stream and yields the `_("Connection error: {0}")`
  frame from `:594-595`.
- **The ingress timeout, and it is smaller than all of the above.** Terraform never sets
  `timeout_sec` on either backend service (`infra/configs/load_balancer.yaml:33-39`; a repo-wide
  grep for `timeout_sec` under `infra/` returns only health-check timeouts), so GCP applies its
  default of **30 seconds**, defined as *the maximum time between the load balancer sending the
  first byte of a request to the backend and the backend returning the last byte of the response*
  (<https://docs.cloud.google.com/load-balancing/docs/https/request-distribution>). The SSE relay
  is a **plain HTTP response**, not a WebSocket, so that 30 s is a **total request→response
  budget** regardless of how much data is flowing. **A Triton answer that takes longer than the
  backend timeout is cut off by the load balancer, and because the widget's failure modes are all
  in-band SSE frames, the client cannot distinguish "the LB cut us off" from "the stream ended
  normally."** This is an **existing** defect (D-9), inherited by any streaming work, not created
  by it. See VERIFY V-4.

---

## 5. The sources chip row — and the phase-blocking answer

### 5.1 First: the ERPNext widget has no sources dropdown. It never did.

**The Phase 0 prompt has a defect here, and the answer only makes sense once it is corrected.**
Phase 0 §4.B and §7 both call this "the sources dropdown". The ERPNext widget has **no dropdown,
no accordion and no collapse**. `renderSources` (`triton_widget.js:1085-1106`) builds a **flat,
always-visible, non-collapsible chip row**:

```js
const box = document.createElement("div");
box.className = "triton-sources";
sources.forEach((s) => {
  const label = s.label || s.title || s.url || "source";
  let a;
  if (s.url) { a = document.createElement("a"); a.href = s.url; a.target = "_blank"; a.rel = "noopener"; }
  else { a = document.createElement("span"); }
  a.className = "triton-source";
  a.textContent = label;
  a.title = label;
  box.appendChild(a);
});
container.appendChild(box);
```

| Property | Reality | Line |
|---|---|---|
| Fields consumed | `s.label`, then `s.title`, then `s.url`, then the literal `"source"`; and `s.url` | `:1090`, `:1092-1096` |
| Fields **never read** | **`s.kind` and `s.subtitle`** — which Triton always populates. No per-kind icon, no subtitle line, no grouping. | — |
| No-`url` source | renders as an inert `<span>`, not a link | `:1097-1098` |
| Sort order | **none.** Insertion order only, i.e. Triton's array order (tool-call chronological, then filtered context — §5.3). No client-side dedup. | `:1089` |
| Escaping | `textContent` and the `.title` property; no `innerHTML`, so labels are inherently safe. `rel="noopener"` only — **not** `noopener noreferrer`, unlike the markdown sanitizer (§6). | `:1096`, `:1101-1102` |
| "When does it open?" | It does not open. It is appended to `live.wrap` (the message container, **not** the bubble) the moment sources arrive, and stays. `.triton-sources { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px }` (`triton_widget.css:345-350`), chips at `:351-367`. | — |
| Idempotence | the `done` handler renders sources **only if none are already present**: `if (meta.sources && !live.wrap.querySelector(".triton-sources"))`. Same guard for charts (`:1366`). So a (currently non-existent) mid-stream `sources` event would win and the `done` payload would be discarded. | `:1363` |
| Reload from history | `renderHistoryMessage` reads `m.ui_metadata.sources` and calls the same renderer, so chips survive a refresh via Triton's persisted `ui_metadata`. | `:1200` |

**The dropdown the prompt describes exists — in the Triton web app.** A collapsible accordion
labelled "{n} Sources" rendering `kind`, `label` and `subtitle || url` per row in a 1/2-column
grid: `triton:frontend/src/views/ChatView.vue:363-381`. **The ERPNext widget is a strictly poorer
renderer of the identical payload.** Any acceptance criterion phrased as "the sources dropdown"
is describing the Triton SPA, not what ERPNext users see today, and must be restated before it
can be scored.

### 5.2 The exact payload shape

The nearest thing to a canonical definition is the module docstring at
`triton:backend/app/core/sources.py:4-10`, quoted as written:

```
Source shape:
    {
        "kind": "erpnext" | "drive" | "gmail" | "calendar" | "context",
        "label": str,        # human-readable title
        "url": str,          # clickable link (empty if none could be built)
        "subtitle": str?,    # optional second line
    }
```

**The TypeScript mirror does not agree with it, and the docstring is the one that is wrong.**
`triton:frontend/src/views/ChatView.vue:1323-1328` declares
`kind: 'erpnext' | 'drive' | 'gmail' | 'calendar' | 'wiki' | 'context'` (`:1324`) — it lists
`wiki`, the Python docstring omits it, and **the builders do emit `wiki`**: the
`sfo_search_knowledge` tool branch at `triton:.../sources.py:178` and the `wiki`/`wikifile`
context branch at `:229-240` (the literal at `:236`). So the emitted set is
`erpnext | drive | gmail | calendar | wiki`, and `"context"` is the reverse case — it appears in
both type declarations but no builder emits it.

Consequence for §14's **C-14** (`test_source_shape_is_stable`): assert against the **emitted**
set — enumerate what the builders actually produce — not against this docstring, or the test
locks in the docstring's omission and turns a correct `wiki` chip into a failure.

Per-kind URL construction: ERPNext `{FRAPPE_BASE_URL}/desk/{slug}/{name}`
(`triton:.../sources.py:24-39`, slug = lowercase-hyphenated doctype `:19-21`); Drive
`https://drive.google.com/file/d/{id}/view` (`:42-45`); Gmail
`https://mail.google.com/mail/u/0/#all/{id}` (`:48-51`); Calendar `htmlLink` if present else
`https://calendar.google.com/calendar/event?eid={id}` (`:54-57`, `:167`).

> **Note for any inline-citation work:** the ERPNext URL is `/desk/{slug}/{name}`, **not**
> `/app/{slug}/{name}`. The Triton code itself flags this as unusual —
> `triton:.../sources.py:26-28`: *"Modern Frappe (v13+) uses /app/Form/{doctype}/{name}, but we use
> /desk/{slug}/{name} here as requested."* See VERIFY V-5: if `/desk/...` does not resolve on
> v16, **every ERPNext source chip in the widget is already a dead link** and inline citations
> would inherit the bug.

### 5.3 ALL RETRIEVED or ONLY CITED? — the answer, and it is neither

**Answer: it is a hybrid, and the rule is PATH-DEPENDENT — it differs by source origin AND by
which execution path Triton chose for that particular turn. It is decided entirely Triton-side;
the ERPNext widget renders whatever array it is handed, in the order it is handed, with no
filtering, sorting or dedup of its own.** And — restating §5.1 in the same breath, because the
two findings are inseparable — **there is no dropdown in ERPNext to attach that semantics to; it
is an always-open chip row.**

The rule is `merge_and_filter`, `triton:backend/app/core/sources.py:286-299`:

```python
def merge_and_filter(tool_sources, context_sources, final_text):
    """Always include tool-call sources; only include context (semantic-search) sources
    whose identifier is actually mentioned in the final response.

    Why: semantic search returns top-K nearest neighbors that the AI may not have
    used at all. Surfacing them as 'sources' was producing hallucinated citations.
    """
    text_lower = (final_text or "").lower()
    kept_context = [s for s in context_sources if _referenced_in_text(s, text_lower)]
    return dedupe(list(tool_sources) + kept_context)
```

1. **Tool-call sources: ALL RETRIEVED, unconditionally.** Built by `from_tool_call`
   (`triton:.../sources.py:73-186`) from the tool's *arguments and results* — never from the
   model's answer. `fac_list_documents` emits **one chip per returned row** (`:91-110`);
   `gws_search_drive` one per file (`:124-134`); `gws_list_emails` one per message (`:145-157`).
   A 20-row list query yields 20 chips whether or not the model mentioned a single one.
2. **Semantic-search (RAG) sources: ONLY CITED — by a substring heuristic, not by a real citation
   mechanism.** `_referenced_in_text` (`triton:.../sources.py:273-283`) lower-cases the final
   answer and asks whether the source's `label`, its `subtitle`, or **the trailing path segment
   of its URL** (`:281`) appears in it, with a `len >= 3` floor (`:282`). It is a plain `in`
   substring test with **no token boundaries**. **Known false-positive mode: `"Pond A"` matches
   inside `"Pond Alpha"`**, so a retrieved-but-unused source whose label is a prefix of any word
   the model happened to write is kept and presented as a citation. The fragility is acknowledged
   in-code at `:230-234`, where the wiki builder deliberately uses the page *route* as `subtitle`
   instead of a constant like "Company Knowledge Base", because a constant the model is
   encouraged to say would keep every retrieved-but-unused wiki chip alive.
3. **`dedupe` (`triton:.../sources.py:258-270`) silently DROPS every source with an empty `url`.**
   A source Triton built but could not link is never shown — which also means the widget's
   `<span>` fallback for url-less sources (`triton_widget.js:1097-1098`) is unreachable via the
   normal path.
4. **Path-dependence — the single most important nuance, and the reason "all-retrieved vs
   only-cited" has no single answer:**

| Path | Call site | Sources semantics |
|---|---|---|
| **Deployed Reasoning Engine** (the default from ERPNext) | `triton:backend/app/core/reasoning_engine.py:249` — `merge_and_filter(tool_sources, [], "".join(text_chunks))` | context sources **hard-coded to `[]`**. Purely "all retrieved tool sources", with **no citation filtering at all**. |
| **In-process intelligence** | `triton:backend/app/core/intelligence.py:1195-1197` — `merge_and_filter(tool_sources, context_sources, "".join(final_text_chunks))` | both lists, so the hybrid rule applies. Note `:1198` then does `sources.extend(event.get("sources", []))` — an **unfiltered** append on top of the filtered merge. |
| **Orchestrator** | `triton:backend/app/core/orchestrator.py:221-225` — `sources_builder.dedupe(collected_sources)` | plain `dedupe`, i.e. **no filtering whatsoever**. The widget never sets `use_orchestrator`, so this is unreachable from ERPNext today. |

  Which path runs is decided **per turn** at `triton:backend/app/api/v1/endpoints/streaming.py:336-363`:
  the in-process fallback is forced by `use_search` / `use_maps` / `use_deep_research` (`:341`),
  by **any non-empty `persona_key`** (`:348-350` — a deployed agent's instruction is baked in at
  deploy time and ADK's templater raises `KeyError` on a bare `{identifier}`), or by an active
  QuickBooks connection (`:355-363`).

  **Therefore: a user with a persona selected in the ERPNext widget gets different sources
  semantics than a user without one.** The widget always sends `persona_key`
  (`triton_widget.js:1285`) — `""` when none is chosen, which is falsy in Python, so the default
  from ERPNext is the **deployed** path (all-retrieved, no filtering).

**Consequence, stated for the phase that will introduce inline citations.** The file that owns
this decision is `triton:backend/app/core/sources.py`, function `merge_and_filter` at line 286.
Introducing a real citation-id protocol (a `[[ref:N]]` marker, say) **changes behaviour on
multiple paths, and all of them must change together** — `reasoning_engine.py:249` (today
unfiltered because it passes `[]`), `intelligence.py:1195` (today substring-filtered, plus the
unfiltered `extend` at `:1198`), and `orchestrator.py:224` (today unfiltered). Changing one and
not the others produces a UI where citations work or don't depending on whether the user had a
persona selected, with no visible cause. On the ERPNext side the render points that must move
together are `triton_widget.js:1085-1106` (the renderer), `:1342-1344` (the dead standalone
`sources` handler), `:1363` (the live `done` path and its idempotence guard) and `:1200`
(history replay).

---

## 6. Markdown / HTML rendering and the sanitizer

### 6.1 Renderer

Not marked, not markdown-it, not hand-rolled. `triton_widget.js:47-53`:

```js
function md(text) {
  try { return frappe.markdown(text || ""); }
  catch (e) { return esc(text || "").replace(/\n/g, "<br>"); }
}
```

`frappe.markdown` is **showdown** with `{ tables: true }`
(`frappe:frappe/public/js/frappe/utils/tools.js:108-135`; converter constructed at `:110`,
`import showdown from "showdown"` at `:4`). It also strips leading newlines (`:113-115`) and
de-indents by the first line's leading whitespace (`:117-131`). The `catch` fallback is a
genuinely different renderer — escape everything, `\n` → `<br>` — and fires only if
`frappe.markdown` throws.

### 6.2 The sanitizer policy, quoted verbatim

`frappe.markdown` returns `sanitize_markdown_html(frappe.md2html.makeHtml(txt))`
(`frappe:frappe/public/js/frappe/utils/tools.js:134`). The policy, from
`frappe:frappe/public/js/frappe/utils/tools.js:38-106`:

```js
const MD_ALLOWED_TAGS = new Set([
  "p","br","hr","h1","h2","h3","h4","h5","h6","ul","ol","li","blockquote",
  "pre","code","em","strong","del","b","i","a","img",
  "table","thead","tbody","tr","th","td",
]);
const MD_SAFE_URL     = /^(https?:|mailto:|tel:|#|\/(?!\/))/i;
const MD_SAFE_IMG_SRC = /^(https?:|\/(?!\/)|data:image\/)/i;
const MD_SAFE_ALIGN   = /^text-align:\s*(left|right|center|justify);?$/i;

// Whitelist-sanitize markdown-generated HTML to prevent XSS (showdown does not sanitize).
function sanitize_markdown_html(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  (function walk(node) {
    for (const el of [...node.children]) {
      const tag = el.tagName.toLowerCase();
      walk(el);
      if (!MD_ALLOWED_TAGS.has(tag)) { el.replaceWith(...el.childNodes); continue; }
      for (const attr of [...el.attributes]) {
        const name = attr.name.toLowerCase();
        const keep =
          name === "title" ||
          name === "alt" ||
          (tag === "a"   && name === "href"  && MD_SAFE_URL.test(attr.value)) ||
          (tag === "img" && name === "src"   && MD_SAFE_IMG_SRC.test(attr.value)) ||
          ((tag === "code" || tag === "pre") && name === "class") ||
          ((tag === "th" || tag === "td") && name === "style" && MD_SAFE_ALIGN.test(attr.value));
        if (!keep) el.removeAttribute(attr.name);
      }
      if (tag === "a") {
        const href = el.getAttribute("href") || "";
        if (href && !href.startsWith("#")) {
          el.setAttribute("target", "_blank");
          el.setAttribute("rel", "noopener noreferrer");
        }
      }
    }
  })(doc.body);
  return doc.body.innerHTML;
}
```

**Everything the inline-citation phase needs, read straight off that policy:**

- `<a>` **is already allowed**. Its `href` survives only if it matches `MD_SAFE_URL` — `http(s):`,
  `mailto:`, `tel:`, `#`, or a **single**-slash root-relative path (`\/(?!\/)` explicitly blocks
  protocol-relative `//evil.com`). `javascript:` and `data:` hrefs are stripped, leaving a bare
  `<a>`.
- Every **non-`#`** link is force-rewritten to `target="_blank" rel="noopener noreferrer"`. So
  `[Task](/app/task/TASK-0001)` opens a **new tab**, unavoidably. A hash route
  `[Task](#Form/Task/TASK-0001)` is left in-tab and is exactly how the desk SPA navigates.
  **If inline citations are to navigate in place, they must be `#`-form hash routes. That is a
  hard constraint of the framework sanitizer, not a stylistic choice.**
- **No `class`, `id`, `data-*`, `style`, `onclick` or any other attribute survives on `<a>`.**
  Only `title` and `alt` are kept globally. So an inline citation **cannot carry a
  `data-source-index`** through `frappe.markdown`. The only channels available are the `href`
  itself and the `title` attribute.
- `class` **is** kept on `<code>` and `<pre>`. That single exemption is why mermaid works —
  showdown emits `<code class="language-mermaid">` and the sanitizer preserves it for
  `querySelectorAll("code.language-mermaid")` (`triton_widget.js:97`). **Do not "simplify" the
  sanitizer interaction; mermaid depends on this one line.**
- Disallowed tags are **unwrapped, not removed** — `el.replaceWith(...el.childNodes)` — so
  `<script>alert(1)</script>` becomes the literal text `alert(1)`, not nothing.
- The walk recurses **before** the tag check, so children are sanitized even when the parent is
  unwrapped.
- **Version caveat.** This sanitizer was added by frappe commit `3d74400e9a` ("fix: sanitize
  frappe.markdown output to prevent XSS", 2026-07-16) and is present on `origin/version-16`
  (verified byte-identical). **Before that commit `frappe.markdown` returned raw showdown output
  with no sanitization at all.** See VERIFY V-1 — if production predates it, the widget is today
  rendering unsanitized model output into `innerHTML` and that is a **live XSS exposure via tool
  results**, not a future design concern.

### 6.3 Everywhere else the widget produces HTML

| Site | Mechanism | Safe? |
|---|---|---|
| user message bubble | `esc(text).replace(/\n/g,"<br>")` (`triton_widget.js:863`) | yes — escaped |
| assistant bubble | `md()` → sanitized (`:961`) | yes, given §6.2 |
| thinking body | `md()` (`:1013`, `:1057`) | yes |
| source chips | `textContent` (`:1101`) | yes |
| step rows | `textContent` (`:952`) | yes |
| status line | `textContent` (`:909`) | yes |
| history rows | `esc()` into a template (`:664-665`) | yes |
| persona rows | `esc()` into a template (`:501-502`) | yes |
| context chips | `esc()` into a template (`:835`) | yes |
| action card | `esc(params.summary ‖ params.tool_name)`, `esc(params.description)` (`:1112-1117`) | yes |
| chart title | `textContent` (`:151`) | yes |
| viz note | `textContent` (`:206`) | yes |
| resolved badge | static + `__()` (`:1139-1141`) | yes |
| error text | `esc()` then through `md()` (`:1261`, `:1374`) | double-escaped — §4.5, defect D-4 |

---

## 7. Conversation history

- **It exists, and it is stored in Triton, not in ERPNext.** The browser holds only the session id
  (`"triton_session_id"`, §2.3). Messages live in Triton's `ChatMessage` table, written by the
  streaming consumer at `triton:.../streaming.py:155-171` (assistant turn, with `ui_metadata`) and
  `:261-267` (user turn).
- **Session id:** an integer Triton `ChatSession.id`. Created lazily by `ensureSession()`
  (`triton_widget.js:1220-1229`) → `start_session` with `{model, persona_key}` (`:1222-1225`) →
  `POST /api/v1/assistant/sessions` (`triton_chat.py:283`) with `title` defaulting to the literal
  **`"ERPNext Chat"`** (`triton_chat.py:278`).
- **That literal title is how ERPNext-originated chats are identifiable in the Triton web app —
  and it is also a defect.** Triton's auto-rename only fires for titles in
  `["New Chat", "New Intelligence Stream"]` (`triton:.../streaming.py:270-273`), so **ERPNext
  sessions are never auto-titled from the first message**. Every ERPNext session shows as
  "ERPNext Chat" in the history overlay, distinguishable only by timestamp
  (`triton_widget.js:656-665`). Defect D-2.
- **Loading:** `loadHistory()` (`:1161-1186`) on first open when a saved id exists;
  `selectSession(id)` (`:670-694`) from the history overlay. Both call
  `get_messages({session_id, limit: 50})` (`:1170`, `:679`) — **a hard-coded limit of 50, no
  pagination and no "load more"**. Older messages are silently unreachable.
- **Replay fidelity** (`renderHistoryMessage`, `:1188-1205`): user messages plain (`:1191-1194`);
  assistant text (`:1196`), `ui_metadata.thinking` (`:1197-1199`), `ui_metadata.sources` (`:1200`),
  `ui_metadata.direct_chart` (`:1201`), `ui_metadata.pending_actions` with their `live_status`
  (`:1202-1204`). **Not replayed:** the tool/agent step timeline (never persisted — `tool_status`
  events are relayed but not collected, `triton:.../streaming.py:108-109`), and
  `ui_metadata.visualization` (Triton persists it at `:148-149`; the widget's history renderer
  never reads it).
- `ui_metadata.system_note` messages are **filtered out of the transcript** (`:1190`) — that is how
  the hidden post-approval continuation stays invisible. Triton stamps it at
  `triton:.../streaming.py:265`.
- **Retention:** no client-side expiry, purge or TTL. See VERIFY V-6 for the Triton side.
- `delete_session` is exposed by the proxy (`triton_chat.py:513-516`) but **the widget never calls
  it** — there is no delete affordance in the history overlay.

---

## 8. Error, empty, offline and loading states

| State | Trigger | What the user sees | Code |
|---|---|---|---|
| Cold empty | boot, `newChat()`, or a history load that returns nothing | centred 🔱 + "Ask Triton anything about your business data." + "Tip: pin the page you're on with “Add this page”." | `:850-857`; `triton_widget.css:456-467` |
| Connecting | every `send()`, before the fetch | italic 11 px muted line above the bubble: "Connecting to Triton…" | `:1248`, `setStatus` `:903-911`; `.css:317-322` |
| Loading a session | `selectSession()` | messages pane replaced with "Loading chat…" | `:677` |
| Loading the history list | `openHistory()` | "Loading…" in the list | `:638` |
| Empty history list | `list_sessions` → `[]` | "No previous chats yet." | `:642` |
| History load failed | `list_sessions` throws | "Couldn't load chat history." | `:648` |
| Loading personas | `openPersonas()` | "Loading…" | `:470` |
| Empty personas | none returned | "No personas yet." | `:475` |
| Personas load failed | throws | "Couldn't load personas." | `:481` |
| Briefing loading | first open of the day | "Preparing your morning briefing…" above an accented card | `:726`; `.css:298-308` |
| Briefing empty/failed | no text, or throws | card removed, cold-empty shown; on failure `LS_BRIEF` is **un-set** so it retries next open | `:733-742` |
| Tool activity | each `tool_status` / `agent_spawn` | ordered timeline; the previous row gets a ✓, the new one spins | `:941-957`; `.css:634-687` |
| Thinking | each `thought` | `<details>` auto-open, shimmering "Thinking" + live `Ns` counter every 500 ms | `:1023-1047`; `.css:598-632` |
| Streaming | text arriving | blinking `▍` caret | `.css:586-596` |
| Mid-stream error | SSE `error` | italic error appended to the answer | `:1372-1376` |
| Transport error | fetch/HTTP failure | `*Error: HTTP 500*` italic appended | `:1259-1262`, `:1289-1291` |
| Action approving | click Approve/Decline | slot replaced with muted "Approving…" / "Declining…" | `:1145` |
| Action failed | confirm/cancel throws | red "Failed: {message}" | `:1156` |
| "Nothing to add" | `addCurrentPage()` with no detectable route | orange `frappe.show_alert`: "Nothing to add from this page." | `:815` |
| Persona save failed | create/update throws | dialog stays open, primary action re-enabled; Frappe's own error dialog shows the server message | `:590-592` |
| Model list unavailable | `list_models` throws | silently keeps the curated fallback list | `:368-375`; `triton_chat.py:335-337` |
| Persona list unavailable | throws | silently keeps whatever is rendered; "Default" always present | `:434-440`; `triton_chat.py:404-405` |
| **Offline** | — | **No dedicated state.** `frappe.call` (behind every `xcall`) shows the framework's orange "Connection Lost / You are not connected to Internet." toast (`frappe:frappe/public/js/frappe/request.js:31-40`). **The stream does not** — `runStream` uses raw `fetch`, so an offline send produces the generic `*Error: Failed to fetch*` italic. | — |
| Mermaid CDN blocked | `ensureMermaid` rejects | the diagram degrades to the raw fenced code block, with no message | `:100-104` |
| Chart lib missing | `frappe.Chart` absent or throws | two-column label/value fallback `<table>` | `:169-174`, `:178-193` |
| Non-inline visualization | `render_visualization` / `render_3d_simulation` | dashed note: "📊 {kind} — open the Triton app to view" | `:197-209` |

---

## 9. Keyboard, focus, accessibility

**Keyboard**

- `Alt+T` / `Alt+Shift+T` toggles the panel — a **`document`-level** `keydown` listener registered
  inside `build()` (`triton_widget.js:299-304`) with `e.preventDefault()`. **It fires from
  anywhere on the page, including while the user is typing in a form field.** It is registered
  only when the widget is enabled, and it is never removed.
- `Enter` sends, `Shift+Enter` newlines (`:291-296`).
- **No `Escape` handler.** Escape does not close the panel or either overlay. (Escape *will* close
  the persona `frappe.ui.Dialog`, because Bootstrap handles that.)
- No shortcut to open history, start a new chat, or focus the composer.

**Focus**

- On open: `state.els.text.focus()` (`:752`).
- On close: **focus is not returned** to the FAB or to whatever had it.
- **No focus trap.** Tab from the composer walks out of the panel into the desk behind it.
- The history/personas overlays are `position:absolute; inset:0` siblings that merely slide in
  (`triton_widget.css:485-496`); the panel content behind them stays in the tab order and remains
  focusable and clickable.
- Persona rows are `<div tabIndex=0>` (`:488-490`) — focusable — but the click handler is on an
  inner `.triton-persona-label` (`:503`) and **there is no `keydown` handler**, so a keyboard user
  can focus a persona row and cannot activate it. Defect D-7.

**ARIA**

- **Zero ARIA attributes in the entire widget.** No `role="dialog"`, no `aria-modal`, no
  `aria-label`, no `aria-live` on the messages region, no `aria-expanded` on the overlays, no
  `aria-busy` while streaming.
- Accessible names come only from `title` attributes on buttons: "Ask Triton (Alt+T)" (`:215`),
  "Choose persona" / "Choose model" (`:226-227`), "Chat history" / "New chat" / "Close"
  (`:228-230`), "Back" (`:242`, `:249`), "New persona" (`:251`), "Attach the page you're viewing"
  (`:233`), "Send" (`:238`). `title` is a weak accessible name and is not announced consistently.
- Button labels are bare emoji/glyph text nodes (🔱 ✎ ✕ 🕘 ← ＋ ➤ 🗑 ⧉) with no text alternative
  beyond `title`.
- The composer `<textarea>` has a `placeholder` ("Ask about your data…", `:237`) and **no
  `<label>`**.
- Streaming text is injected with no live region, so a screen reader announces nothing as the
  answer arrives.
- **What is handled well:** `prefers-reduced-motion` is respected in both JS (`:62-63`, `:992-999`)
  and CSS (`.css:85-97`, `:690-701`), including dropping the typewriter pump entirely rather than
  merely disabling animations. And the thinking disclosure uses a real `<details>`/`<summary>`
  (`:1025-1031`) with the marker hidden via `list-style:none` + `::-webkit-details-marker{display:none}`
  (`.css:599-607`), so native semantics are preserved.

---

## 10. Mobile / responsive, with the CSS breakpoints

- **One breakpoint: `@media (max-width: 480px)`** (`triton_widget.css:559-578`):
  - FAB moves to a 16 px inset (`:560-563`).
  - Panel goes near-fullscreen: `right/left/top/bottom: 8px; width:auto; max-width:none;
    height:auto; max-height:none` (`:564-573`).
  - `.triton-title` (the word "Triton") is **hidden** to free header width for the two `<select>`
    pickers (`:574-577`).
- Desktop sizing: `width:410px; max-width:calc(100vw - 32px); height:640px;
  max-height:calc(100vh - 120px)` (`:51-54`) — so on a mid-size viewport it already shrinks
  fluidly before the breakpoint.
- Header pickers are capped at `max-width:92px` each, with an explicit comment that 92 (not 104)
  is what lets both sit beside the icon buttons inside a 410 px header (`triton_widget.css:121-137`).
- Native `<select>` elements are used **deliberately** to get the OS dropdown on mobile
  (`triton_widget.css:121-123`). **A rewrite to a custom listbox loses that.**
- Composer auto-grows to a 120 px cap (`triton_widget.js:311-315`; CSS `max-height:120px`,
  `min-height:38px`, `.css:434-435`).
- Message bubbles `max-width:90%` (`.css:274`); the briefing card overrides to 100% (`.css:300-303`);
  action cards `width:92%` (`.css:371`).
- Long content: `.triton-msg { word-wrap: break-word }` (`.css:277`); code blocks
  `white-space:pre-wrap; overflow-x:auto` (`.css:309-315`); mermaid holder `overflow-x:auto`
  (`.css:744`); source chips ellipsis (`.css:359-362`).
- **No touch gestures.** No swipe-to-close, no pull-to-refresh, no long-press.
- **No dark-theme handling of its own.** The panel inherits desk CSS variables with hard-coded
  fallbacks — `var(--card-bg,#fff)`, `var(--text-color,#1f272e)`, `var(--border-color,#e2e6e9)`,
  `var(--subtle-fg,#f1f3f5)`, `var(--primary,#1f6feb)`, `var(--text-muted,#8d99a6)`,
  `var(--control-bg,#fff)` (e.g. `.css:55-57`, `:290-292`, `:436-437`). The header and FAB
  gradients are **hard-coded** `linear-gradient(135deg,#1f6feb,#0a3d91)` (`.css:20`, `:104`, `:503`)
  and do not follow the desk theme. Mermaid diagrams deliberately stay on a light canvas in both
  themes (`public/js/global_enhancements/mermaid_theme.js:12-17`).

---

## 11. Vue — the two-copies hazard does not exist today, and a bundled-Vue SPA would create it

**The widget is 100% vanilla JS. It does not use Vue at all.** Verified:
`rg -n "Vue" erpnext_enhancements/public/js/global_enhancements/triton_widget.js` returns nothing.
The only DOM APIs it uses are `document.createElement`, `innerHTML`, `appendChild` and
`addEventListener`.

**How Vue reaches the page today:**

- `hooks.py:49` — `"/assets/erpnext_enhancements/js/vue.global.js"` as a **raw** `app_include_js`
  entry, listed **first**, before both bundles.
- The vendored file is **Vue 3.5.26**, the UMD/global build (`public/js/vue.global.js:2` header
  `vue v3.5.26`; the global assignment is `window.Vue = Vue;` at `public/js/vue.global.js:18411`).
  **592 KB unminified.**
- It is raw-not-bundled **on purpose**, and the reason is written down twice — `hooks.py:44-48` and
  `public/js/erpnext_enhancements.bundle.js:17-23`: importing a UMD build from an esbuild bundle
  **captures its exports instead of letting it set `window.Vue` / `window.Gantt`**, and their
  content never changes so the immutable `/assets` cache cannot serve them stale. This is ADR 0008.
- It is **also** re-listed defensively in **fourteen** `doctype_js` entries (`hooks.py:108`, `:153`,
  `:163`, `:168`, `:170`, `:182`, `:184`, `:189`, `:197`, `:203`, `:209`, `:216`, `:222`, `:228`).
  Frappe de-duplicates asset loads, so these are belt-and-braces for form-script ordering.
- Existing consumers read the global **lazily and behind a guard**: `public/js/comments.js:33`
  `if (typeof window.Vue === 'undefined')`, then `:49` `window.Vue.createApp({...})`; the same
  pattern in `public/js/project_enhancements.js:102`, `:124`.

**So, plainly: today there is exactly ONE Vue runtime on a desk page, reachable only as
`window.Vue`. Nothing `import`s Vue as an ES module anywhere in this repo. The two-Vue-copies
hazard DOES NOT EXIST TODAY. It would be CREATED by a bundled-Vue SPA.**

A second, independent Vue runtime is not automatically fatal — two Vue 3 apps can coexist if they
never share reactive objects or components — but the concrete failure modes are:

1. **Two reactivity systems.** A `ref`/`reactive` created by copy A is an ordinary object to copy
   B. If the SPA ever hands a reactive value to `comments.js` (or vice versa) it silently stops
   being reactive. No error; it just never updates.
2. **Two `app.provide`/`inject` registries, two `AppContext`s.** Anything relying on
   `getCurrentInstance()` across the boundary returns `null`.
3. **Payload.** ~592 KB of vendored UMD Vue loads on **every desk page** regardless of use, plus
   the SPA's own bundled copy on top.
4. **Version skew.** The vendored copy is pinned at 3.5.26 by file content; an SPA on a different
   3.x produces two subtly different runtimes with no build-time warning.
5. **Order coupling.** `vue.global.js` is `app_include_js[0]`, and the UMD assignment at
   `public/js/vue.global.js:18411` **overwrites** whatever `window.Vue` already holds. If an SPA
   bundle ran first and set `window.Vue` to its own copy, the vendored file would clobber it. This
   is the exact bug class already documented in this repo for `window.Gantt`
   (`public/js/erpnext_enhancements.bundle.js:88-92`, and the DHTMLX loader's save/restore
   bracket).

**Coexistence options, with the trade-off of each:**

| Option | What it is | Trade-off |
|---|---|---|
| **(a) Externalize Vue to the window global** | mark `vue` as external in the SPA's bundler and alias it to `window.Vue`, so exactly one runtime exists | **The only option that eliminates the hazard rather than managing it.** Cost: it **pins the SPA to Vue 3.5.26** — the SPA cannot adopt a newer Vue without re-vendoring `vue.global.js` and re-testing `comments.js` and `project_enhancements.js` against it. Also couples the SPA build to a load-order guarantee it does not control. |
| **(b) Shadow-root (or iframe) isolation** | mount the SPA in a shadow root, never expose or consume a Vue object across the boundary, accept the double payload | Manages the hazard rather than removing it, and inherits a documented problem: `public/css/desk_addons.bundle.scss:30-34` records that a document-level stylesheet **cannot cross into a shadow DOM**, which is why the Gantt widget links its own hashed CSS into whichever root node it mounts in. **An SPA in a shadow root loses all desk CSS variables** — which the widget currently depends on for theming (§10) — and must ship its own theme bridge. |
| **(c) Retire the vendored UMD** | migrate `comments.js` and `project_enhancements.js` to import Vue from the SPA bundle, then delete `hooks.py:49` **and all fourteen `doctype_js` copies** | Cleanest end state, largest blast radius: two existing Vue features and fifteen hook entries change in one PR, and any other consumer of `window.Vue` (including ones outside this repo) breaks silently. |

VERIFY V-7 (§15) is the one cheap unknown that could change the recommendation.

---

## 12. Telemetry and logging

- **The widget emits nothing.** No `console.log`/`warn`/`error`, no `frappe.log_error`, no
  analytics beacon, no timing metric, no `frappe.telemetry` call. Verified by grep. Every `catch`
  in the file is either silent or renders a user-visible message, and several carry explicit
  comments saying so (`:85`, `:373`, `:438`, `:606`, `:620`).
- The FAB is not instrumented: **there is no ERPNext-side record of opens, closes, model choice,
  persona choice or message counts.**
- **Server-side, only when `debug_logging` is on:** three `frappe.log_error` calls into the
  "Triton Chat" Error Log title — bridge-token failure (`triton_chat.py:147-148`), non-2xx on a
  JSON call (`:182-183`), non-200 on the stream (`:585-589`). Each truncates the upstream body to
  500 chars. Note the stream one is itself wrapped in `try/except Exception: pass` (`:586-589`)
  because it runs inside the lazy response body after request teardown (§4.1).
- **Triton logs considerably more,** all server-side: one `logger.info` per streaming query
  (`triton:.../streaming.py:321`), bridge mint / auto-provision / reject
  (`triton:.../erpnext_bridge.py:102`, `:113`, `:129`), source-builder warnings
  (`triton:backend/app/core/sources.py:184`, `:242`), and the Reasoning-Engine stream summary
  including the final source count (`triton:backend/app/core/reasoning_engine.py:250-253`).
- **Token accounting** is persisted per turn as a `ModelUsage` row (`triton:.../streaming.py:163-170`),
  attributed to the bridged Triton user — so ERPNext widget usage already appears in Triton's
  usage reporting.
- **Privacy note.** The pinned-page preamble (§13 context section) sends doctype names, document
  names and **list/report filter values** to Triton on every turn where a chip is present. Filters
  can contain customer names, amounts and dates. This is the widget's only egress of business data
  that the user did not type.

### Context pinning — the behaviour that carries that data

Recorded here because it is the mechanism behind the privacy note and because a naive refactor
drops it silently.

- **`detectPageContext()` — four route shapes** (`triton_widget.js:765-806`):
  `Form/{doctype}/{name}` → `{type:"document", doctype, name, title, route}` (`:771-785`);
  `List|list/{doctype}` → `{type:"list", doctype, filters, title, route}` (`:786-797`) with
  filters from `cur_list.get_filters_for_args()` (`:791-792`); `List/{doctype}/Report` →
  `{type:"report", report_name, name, filters, …}` (`:793-795`); `query-report/{name}` →
  `{type:"report", …}` with `frappe.query_report.get_filter_values()` (`:798-804`); anything else →
  `{type:"page", title: document.title minus " | …", route}` (`:805`). Every `cur_frm`/`cur_list`/
  `query_report` read sits inside its own `try/catch` (`:779-783`, `:790-792`, `:800-802`),
  defensive against desk internals changing.
- **Chips are consumed after one turn.** After a successful non-hidden send they are cleared so
  context is not silently re-sent on every message (`:1255-1258`). Hidden continuations always
  send `context: "[]"` (`:1280`).
- **The preamble the model actually sees** (`triton_chat.py:230-236`): `"[ERPNEXT PAGE CONTEXT]
  The user is currently viewing the following in ERPNext. Use your ERPNext tools to fetch live
  details as needed when they are relevant to the question; do not assume values you have not
  fetched:\n"` + one `- ` line per ref + `"\n\n"`, prepended to the prompt. **References only,
  never document bodies** (`:195-197`). Malformed JSON in `context` silently degrades to no
  preamble (`:204-206`).

### Other behaviour a naive refactor would drop

1. **The morning-briefing hijack.** The first panel open of each *local calendar* day does **not**
   restore your last chat: it clears `state.sessionId`, removes `"triton_session_id"` from
   localStorage, wipes the message pane and renders the briefing as the opening assistant message
   (`:715-743`). The previous conversation is only reachable via the history overlay afterwards.
   `LS_BRIEF` is written **before** the fetch (`:716`) and **removed again on failure** (`:741`) so
   a failed briefing retries on the next open instead of burning the day's slot (`:738`).
   `todayStr()` (`:697-706`) builds the date from the **browser's local** timezone, not the site
   timezone, so a user in another timezone gets the briefing at a different wall-clock moment than
   Triton's 06:30 America/Denver warm-up job
   (`triton:backend/app/api/v1/endpoints/assistant.py:141-144`).
2. **Approve → hidden continuation.** Approving an action card fires a *second, invisible* chat
   turn with the literal English string `"The proposed action was approved. Please proceed."` and
   `hidden: true` (`:1153`), so Triton executes and reports back, mirroring the Triton web app
   (`:1150-1152`). It is wrapped in `__()` and is therefore translatable — **the model receives
   whatever the user's UI language renders it as.**
3. **Action-card status vocabulary.** `renderResolved` (`:1137-1142`) treats
   `confirmed|executed|approved` as success, `expired` as "Expired", everything else as
   "Declined". High-risk actions get a red left border via `params.risk === "high"` (`:1111`,
   `.css:379-381`). Params shape confirmed at `triton:backend/app/core/actions.py:91-99`:
   `action_id, tool_name, integration, summary, description, risk, args`. The widget reads
   `action_id`, `summary`, `tool_name`, `description`, `risk` — it never surfaces `integration` or
   `args`, so **the user approves a write without seeing what will be written**, only the
   generated summary/description.
4. **Mermaid pipeline** (`:65-126`): lazy `<script>` injection from
   **`https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js`** (`:75`), memoized in a
   module-level promise (`:69-73`), themed through `window.sf_mermaid.init` with a fallback to
   `mermaid.initialize({startOnLoad:false, theme:"default"})` (`:79-83`) and a swallowed theme
   error (`:84-86`). Fenced blocks are converted only **after** the stream finishes (`:998`,
   `:1017`), guarded against double-processing by `pre.dataset.tritonMermaid` (`:107-108`); a
   render failure replaces the diagram holder with a plain `<pre>` of the source (`:120-124`).
   **This is the only third-party network dependency in the widget, and it is an external CDN.**
   No CSP was found anywhere in the app. See VERIFY V-3.
5. **Inline charts** (`:128-193`): Triton ships a **Chart.js-shaped** config
   (`{chart_type, title, data:{labels, datasets:[{label|name, data|values}]}}`) and the widget
   **translates it to frappe-charts** — a `{doughnut|donut→donut, pie, line, bar}` map at `:143`,
   default `bar` at `:144`, `height:220`, `animate: !reducedMotion`, a hard-coded six-colour
   palette `["#1f6feb","#2da44e","#bf8700","#cf222e","#8250df","#0969da"]`, and
   `axisOptions.xIsSeries` only for `line` (`:161-168`). `frappe.Chart` is the desk's bundled
   frappe-charts global (`frappe:frappe/public/js/frappe/ui/chart.js:4`). Falls back to a
   two-column `<table>` when the library is missing or throws (`:169-174`, `:178-193`). **A
   refactor that swaps chart libraries must keep this translation layer or Triton's payload stops
   rendering.**
6. **Deliberately-ignored `ui_command`s:** `voice_dial` and `show_native_plan_approval` are
   Desk-side actions "intentionally not surfaced in the embedded widget" (`:1354-1355`). Triton
   does emit `voice_dial` (`triton:backend/app/core/reasoning_engine.py:360-361`). **Dropping that
   comment in a rewrite risks accidentally wiring a dialer into the chat panel.**
7. **Model-selection precedence** (`:340-354`), five levels in order: the current in-memory pick if
   still listed → saved `"triton_model"` if still listed → `config.default_model` → the literal
   `"gemini-3.5-flash"` → the first option. The hard-coded Flash preference at `:350` is annotated
   "(requested default)". Server-side the curated fallback list is `TRITON_MODELS`
   (`triton_chat.py:39-44`), replaced at runtime by the live list from
   `GET /api/v1/assistant/models` relabelled by `_pretty_model_label` (`:292-305`), and **the live
   list is only accepted if it has more than one entry** (`:335-337`).
8. **Persona-selection precedence** (`:419-431`), three levels: current pick if still listed →
   saved `"triton_persona_key"` if still listed → `""`. Deliberately *not* pinned when the key has
   vanished, so a persona deleted in the Triton web app stops riding along (`:420-421`).
9. **Persona picker structure:** "Default" first (`:401`), then three `<optgroup>`s — "Built in" /
   "Yours" / "Shared", keyed on `is_builtin` and `editable` (`:403-407`) — then a `⚙ Manage…`
   **sentinel** (`:417`, `PERSONA_MANAGE = "__manage__"` at `:387`) which is intercepted and *not*
   treated as a selection (`:449-455`).
10. **Persona row affordances differ by ownership** (`:523-530`): editable → ✎ Edit + 🗑 Delete;
    otherwise → ⧉ Duplicate, because built-ins are frozen constants in Triton (`:527-528`,
    corroborated by `triton_chat.py:470-472`). Duplicating immediately re-opens the edit form on
    the copy (`:604`).
11. **The persona editor is a `frappe.ui.Dialog`** (`:537-595`) with five fields — Name (reqd),
    Emoji, Description (Small Text), System prompt (Long Text, reqd), and a **Check** translated to
    `visibility: "company" | "private"` (`:573`). Editing extracts the numeric id with
    `p.key.split(":")[1]` (`:579`, `:615`). The dialog disables and re-enables its primary action
    around the save (`:575`, `:591`).
12. **`pulse()`** (`:318-323`) restarts a one-shot CSS animation by removing the class, forcing
    reflow with `void el.offsetWidth`, then re-adding it. Used for the `triton-fresh` entrance on
    new chat (`:1217`) and session switch (`:688`). **A rewrite that just toggles the class without
    the reflow gets no animation on the second use.**
13. **`state.messages_loaded`** is read at `:756` but **never declared in the `state` literal**
    (`:27-41`) — it springs into existence at `:678`, `:719`, `:1171`, `:1184`. Its role is to stop
    `loadHistory()` re-running on every open. Easy to lose in a rewrite that types the state
    object.

---

## 13. Defects found and deliberately NOT fixed

Phase 0 writes no code. Each of these is pre-existing; none is caused by the planned work; and a
refactor that "tidies" either half of one without knowing about it will look like it introduced a
regression.

Phases are named per the master phase map (D7): 1 foundations/auth, 2 sync engine, **3 chat SPA**,
4 notifications, **5 Triton integration**, 6 governance/audit/rollout.

| # | Defect | Evidence | Severity | Phase that should own it |
|---|---|---|---|---|
| D-1 | **Unsaved-edit context is dead code.** The widget sets `ref.unsaved = true` when the pinned form is dirty; the server preamble builder looks for `ref.get("dirty_fields")`. The two never agree, so the `"(UNSAVED edits in progress: …)"` branch — and the whole "we pass unsaved edits inline" promise in the module docstring — has **never fired**. | `triton_widget.js:781` vs `triton_chat.py:215-217`; docstring `triton_chat.py:196-198` | Medium (silent feature gap; also a live *privacy* relief, since unsaved values are not in fact egressed) | **5** — implement it or delete both halves, deliberately |
| D-2 | **Every ERPNext-originated Triton session is titled "ERPNext Chat".** Triton's auto-rename only fires for `["New Chat", "New Intelligence Stream"]`, so the history overlay is a wall of identical rows distinguishable only by timestamp. | `triton_chat.py:278`; `triton:.../streaming.py:270-273`; overlay `triton_widget.js:656-665` | Medium (UX) | **5** |
| D-3 | **`enable_write_actions` is inert on the client.** `get_config` returns it and the widget never reads it; action cards render whenever Triton sends a `pending_action` regardless of the setting. The Settings checkbox therefore promises an administrator something it does not deliver. Same for `user` and `full_name`, also returned and never read. | `triton_chat.py:255`, `:260-261`; no reader in `triton_widget.js` | **High** — a governance control that does not control | **6** (governance) with the fix landing wherever the gate belongs; see VERIFY V-8 |
| D-4 | **Error text is double-escaped.** `esc()` is applied to error strings that then pass through `md()`, so an error containing `"` renders as `&quot;`. Dropping the `esc()` is arguably more correct — but only because `md()` sanitizes, which is itself conditional on V-1. | `triton_widget.js:1261`, `:1374`; `frappe:.../utils.js:231-244` | Low | **5** — decide explicitly, do not drift |
| D-5 | **`scrollDown()` has no "user is reading scrollback" guard.** Ten call sites all hard-set `scrollTop = scrollHeight`, so scrolling up mid-stream yanks you back on the next frame. | `triton_widget.js:55-58` and the ten sites listed in §4.4 | Medium (UX; worse in a multi-party chat than in a single-answer assistant) | **3** |
| D-6 | **The personas overlay leaks across a panel close.** S3 and S4 are independent; `toggle(false)` closes only history, so a personas overlay left open is still open behind the next panel open. | `triton_widget.js:459-462`, `:627-630`, `:760` | Low | **3** |
| D-7 | **Persona rows are focusable but not keyboard-activatable.** `<div tabIndex=0>` with the click handler on an inner element and no `keydown` handler. | `triton_widget.js:488-490`, `:503` | Medium (a11y) | **3** |
| D-8 | **No ARIA anywhere, no focus trap, no live region, no Escape handler.** A screen-reader user gets no announcement as an answer streams in; a keyboard user tabs straight out of the panel into the desk. | §9 | Medium–High for a chat product used all day | **3** |
| D-9 | **The GCLB backend-service timeout (default 30 s, never set in Terraform) truncates long streams with no error.** The relay's own budget is 120 s, four times larger. Because all failure modes are in-band SSE frames, the client cannot tell truncation from normal completion. | `infra/configs/load_balancer.yaml:33-39`; `triton_chat.py:75`, `:580`; GCP default per <https://docs.cloud.google.com/load-balancing/docs/https/request-distribution> | **High** for any streaming feature | **1** (infra change + mirror into Terraform) — it must be settled before Phase 5 relies on a streaming budget. Note a raise applied by hand out-of-band would be silently reverted by the next `terraform apply`. |
| D-10 | **Mermaid loads from an external CDN (`cdn.jsdelivr.net`) at runtime.** The only third-party network dependency in the widget. No CSP exists in the app today to permit or deny it. | `triton_widget.js:75`; no `content-security-policy` in app source | Medium (availability + supply chain) | **3** — decide vendor-vs-CDN when the SPA's asset story is settled |
| D-11 | **Triton's `visualization` event is silently dropped by the widget**, and `ui_metadata.visualization` is persisted by Triton but never replayed on history load. | `triton:.../streaming.py:125-129`, `:148-149`; `triton_widget.js:1377-1378`, `:1188-1205` | Low | **5** |
| D-12 | **`renderSources`' url-less `<span>` branch is unreachable** because `dedupe` drops every source with an empty `url` before it ships. Dead code that looks live. | `triton_widget.js:1097-1098`; `triton:.../sources.py:258-270` | Low | **5** |
| D-13 | **The standalone `sources` SSE handler is dead code today** — no Triton path emits that event; the live path is `done.ui_metadata.sources`. It is defensive and worth keeping, but must be knowingly kept, not accidentally deleted or accidentally relied on. | `triton_widget.js:1342-1344` vs `:1363`; grep over `triton:backend/` | Low | **5** |
| D-14 | **`tests/test_triton_personas.py` runs nowhere.** It is a bench-free **pytest** suite; CI has nine `python -m pytest` steps and names every other bench-free pytest suite but not this one. Independently confirmed: `grep -c "test_triton_personas" .github/workflows/ci.yml` → **0**. **The only automated coverage of `triton_chat.py` has never executed.** | `.github/workflows/ci.yml` (nine steps at `:498-501`, `:517-520`, `:528-529`, `:537-538`, `:545-546`, `:549-550`, `:556-557`); `CLAUDE.md` warns about exactly this failure mode | **High** — it is the precondition for §14 meaning anything | **before Phase 3** — see §14 |
| D-15 | **The morning briefing is keyed on the browser's local calendar day**, not the site timezone, so it fires at a different wall-clock moment than Triton's 06:30 America/Denver warm-up job. | `triton_widget.js:697-706`, `:715-743`; `triton:backend/app/api/v1/endpoints/assistant.py:141-144` | Low | **5** |
| D-16 | **Action cards approve a write the user cannot inspect.** `integration` and `args` are in the payload and never rendered; the user sees only a model-generated `summary`/`description`. | `triton_widget.js:1112-1117`; `triton:backend/app/core/actions.py:91-99` | Medium — it sits directly against ADR 0006 ("AI writes need desk confirmation"), whose value depends on the confirmation being informative | **6** |
| D-17 | **`Alt+T` is a document-level handler with `preventDefault()` that fires while the user is typing in any field.** No composition/`isContentEditable` check, never removed. | `triton_widget.js:299-304` | Low | **3** |

---

## 14. The "MUST SURVIVE" table

**Read this first.** The only automated coverage of `triton_chat.py` is
`erpnext_enhancements/tests/test_triton_personas.py`, a bench-free **pytest** suite that is
referenced **nowhere** in `.github/workflows/ci.yml` and has therefore **never run** (defect
D-14). **Today, every behaviour in this table is defended by nothing.** Wiring that suite into CI
is a prerequisite for this table to mean anything, and it is two lines appended to the
`unit-tests` job:

```yaml
      - name: Triton persona proxy + SSE relay (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_triton_personas.py -q
```

Every **new** bench-free pytest suite named below needs its **own** step of exactly that shape —
one file, `-q`, a descriptive `name:` — because each suite installs its own `frappe` stub and a
separate process keeps them from cross-talking (`ci.yml:261-263`). It must **not** be appended to
an existing invocation, and it must stub `requests` the way `test_triton_personas.py` already does
(`:68-71`), because the CI runner installs only `httpx pytest jinja2` (`ci.yml:144`).

`test_triton_chat_proxy.py`, `test_triton_widget_stream.py`, `test_triton_widget_sources.py` and
`test_triton_widget_charts.py` are **new** bench-free pytest modules. `e2e/…spec.ts` entries are
**new** Playwright specs against a real bench; the repo has **no JS test runner configured today**
and there is **no test of any kind for `triton_widget.js`**, so standing up that harness is itself
Phase 3 work and should be its first commit after the CI wiring above.

**Phase numbering note (a contradiction, recorded rather than resolved).** The sibling audit note
that supplied this material used a local legend (`P1` page-shell, `P2` SPA+Vue, `P3` streaming,
`P4` markdown, `P5` sources, `P6` state) which is **not** the master phase map. Per D7 this
document uses the master map — **1** foundations/auth, **2** sync engine, **3** chat SPA, **4**
notifications, **5** Triton integration, **6** governance/audit/rollout — and the rows below are
remapped accordingly. Anything touching the widget's shell, mount, Vue, DOM or interaction model
is **Phase 3**; anything touching the Triton wire, streaming, markdown or citations is **Phase 5**.

### 14.1 Streaming — every row names a specific test

| # | Behaviour that must survive | Evidence | Phase that risks breaking it | Named regression test |
|---|---|---|---|---|
| S-1 | `stream_query` returns a werkzeug `Response` with `mimetype="text/event-stream"` and headers `Cache-Control: no-cache`, `X-Accel-Buffering: no` | `triton_chat.py:597-601` | 5 | `test_triton_chat_proxy.py::test_stream_response_is_sse_with_no_buffering_headers` |
| S-2 | Upstream chunks are relayed byte-for-byte, never re-framed or re-encoded | `triton_chat.py:591-593` | 5 | `test_triton_chat_proxy.py::test_stream_relays_chunks_verbatim` |
| S-3 | The generator captures token/url/payload/timeout **before** the Response is returned; the lazy body touches no Frappe globals | `triton_chat.py:545-547`, `:553-567` | 5 | `test_triton_chat_proxy.py::test_stream_body_touches_no_frappe_globals_after_teardown` |
| S-4 | Upstream non-200 yields a well-formed `{"type":"error"}` SSE frame, not an HTTP 500 | `triton_chat.py:582-590`, `_sse_error` `:535-536` | 5 | `test_triton_chat_proxy.py::test_upstream_error_becomes_sse_error_frame` |
| S-5 | A transport exception yields the `Connection error:` SSE frame | `triton_chat.py:594-595` | 5 | `test_triton_chat_proxy.py::test_transport_exception_becomes_sse_error_frame` |
| S-6 | Request timeout is `(15, request_timeout)` — separate connect and read | `triton_chat.py:580` | 5 | `test_triton_chat_proxy.py::test_stream_uses_split_connect_and_read_timeouts` |
| S-7 | Body carries exactly `session_id, prompt, context, hidden, model, persona_key`; `persona_key:""` is sent, an omitted `persona_key` is absent | `triton_widget.js:1277-1286`; `triton_chat.py:558-565` | 3, 5 | **existing** `test_triton_personas.py::test_stream_query_payload_carries_persona_key`, `::test_stream_query_forwards_empty_persona_key_as_the_default_voice`, `::test_stream_query_omits_persona_key_when_not_supplied` — **must be wired into CI first (D-14)** |
| S-8 | Frames split on `\n\n`; only `data:` lines parsed; the `: ping` comment ignored; unparseable frames swallowed | `triton_widget.js:1296-1322` | 3, 5 | `test_triton_widget_stream.py::test_frame_parser_splits_and_ignores_comments` — extract the parser into a testable module as part of the work |
| S-9 | All nine handled event types dispatch correctly and unknown types are ignored, not thrown | `triton_widget.js:1324-1380` | 3, 5 | `test_triton_widget_stream.py::test_every_event_type_dispatches` |
| S-10 | Typewriter pump: 2–60 chars/frame, `ceil(remaining/3)`, on `requestAnimationFrame` | `triton_widget.js:973-982` | 3 | `e2e/triton_stream.spec.ts::renders_tokens_progressively_not_all_at_once` |
| S-11 | Reduced motion bypasses the pump entirely (text snaps in) and CSS drops caret/shimmer/spin | `triton_widget.js:62-63`, `:992-999`; `triton_widget.css:690-701` | 3 | `e2e/triton_stream.spec.ts::reduced_motion_snaps_text_and_disables_animations` |
| S-12 | The first `text` event collapses the thinking disclosure and settles all active tool steps | `triton_widget.js:987-990` | 3, 5 | `e2e/triton_stream.spec.ts::first_text_settles_thinking_and_steps` |
| S-13 | `done` recovers the full answer from `ev.content` when no `text` events arrived | `triton_widget.js:1360-1362` | 5 | `test_triton_widget_stream.py::test_done_recovers_text_when_stream_had_none` |
| S-14 | `finishStreaming` cancels both rAFs, flushes text, runs mermaid, drops `.triton-streaming` | `triton_widget.js:1003-1020` | 3, 5 | `e2e/triton_stream.spec.ts::stream_end_flushes_and_removes_cursor` |
| S-15 | The `finally` always re-enables the composer — no path leaves it locked | `triton_widget.js:1263-1266` | 3, 5 | `e2e/triton_stream.spec.ts::composer_reenabled_after_mid_stream_error` |
| S-16 | Concurrent sends refused while streaming; session switch refused while streaming | `triton_widget.js:1241`, `:671` | 3 | `e2e/triton_stream.spec.ts::second_send_is_ignored_while_streaming` |
| S-17 | Closing the panel mid-stream does not abort it; reopening shows the completed answer | no abort path — `triton_widget.js:746-762`, no `AbortController` in the file | 3 | `e2e/triton_stream.spec.ts::closing_panel_midstream_preserves_the_answer` |
| S-18 | Tool/agent step timeline: each new status ✓s the previous and appends an active row; duplicates suppressed | `triton_widget.js:941-957` | 3, 5 | `e2e/triton_stream.spec.ts::tool_steps_accumulate_and_settle` |
| S-19 | Thought text coalesces to one markdown re-render per frame | `triton_widget.js:1049-1061` | 3 | `e2e/triton_stream.spec.ts::thoughts_render_once_per_frame` |
| S-20 | Mermaid runs only after the stream completes, never mid-pump | `triton_widget.js:998`, `:1017` | 3, 5 | `e2e/triton_stream.spec.ts::mermaid_renders_only_after_done` |

### 14.2 Sources / citations — every row names a specific test

| # | Behaviour that must survive | Evidence | Phase that risks breaking it | Named regression test |
|---|---|---|---|---|
| C-1 | Tool-call sources are included **unconditionally** — all retrieved, never filtered by the answer text | `triton:backend/app/core/sources.py:286-299` | 5 | `triton: backend/tests/test_sources.py::test_tool_sources_are_never_filtered_by_the_answer_text` |
| C-2 | RAG/context sources are kept only when label/subtitle/url-tail appears in the final text (`len>=3`, substring, case-insensitive) — including the documented false-positive behaviour | `triton:.../sources.py:273-283` | 5 | `triton: backend/tests/test_sources.py::test_context_sources_kept_only_when_referenced` |
| C-3 | The Reasoning-Engine path passes `[]` for context sources — no citation filtering at all on the ERPNext default path | `triton:backend/app/core/reasoning_engine.py:249` | 5 | `triton: backend/tests/test_reasoning_engine.py::test_re_path_emits_only_tool_sources` |
| C-4 | A non-empty persona forces the in-process path, which changes sources semantics for that turn | `triton:.../streaming.py:348-350` | 5 | `triton: backend/tests/test_streaming_routing.py::test_persona_forces_inprocess_path` |
| C-5 | `dedupe` drops url-less sources and de-dupes preserving order | `triton:.../sources.py:258-270` | 5 | `triton: backend/tests/test_sources.py::test_dedupe_drops_urlless_and_preserves_order` |
| C-6 | Wiki sources use the **route** as `subtitle`, never a constant phrase (the anti-false-positive measure) | `triton:.../sources.py:229-240` | 5 | `triton: backend/tests/test_sources.py::test_wiki_subtitle_is_route_not_constant` |
| C-7 | The widget renders `label ‖ title ‖ url ‖ "source"` and links only when `url` is truthy | `triton_widget.js:1089-1103` | 3, 5 | `test_triton_widget_sources.py::test_source_chip_label_fallback_chain` |
| C-8 | Chip order = server array order; no client-side sort or dedup | `triton_widget.js:1089` | 3, 5 | `test_triton_widget_sources.py::test_chip_order_matches_payload_order` |
| C-9 | `done` renders sources only if none are already present (the idempotence guard) | `triton_widget.js:1363` | 5 | `test_triton_widget_sources.py::test_done_does_not_duplicate_already_rendered_sources` |
| C-10 | Sources replay from `ui_metadata.sources` when a session is reopened | `triton_widget.js:1200`; `triton:.../streaming.py:146-147` | 3, 5 | `e2e/triton_sources.spec.ts::sources_survive_a_session_reload` |
| C-11 | Chips use `textContent` + the `title` property, never `innerHTML` | `triton_widget.js:1101-1102` | 3, 5 | `test_triton_widget_sources.py::test_source_labels_are_not_html_injected` |
| C-12 | Source links are `target="_blank" rel="noopener"` | `triton_widget.js:1094-1096` | 3, 5 | `test_triton_widget_sources.py::test_source_links_open_in_new_tab_with_noopener` |
| C-13 | A standalone `sources` SSE event, if ever emitted, still renders and wins over the `done` payload | `triton_widget.js:1342-1344` + `:1363` | 5 | `test_triton_widget_stream.py::test_midstream_sources_event_suppresses_done_sources` |
| C-14 | The `Source` wire shape stays `{kind,label,url,subtitle?}` across ERPNext and the Triton SPA | `triton:.../sources.py:4-11`; `triton:frontend/src/views/ChatView.vue:1323-1328` | 5 | `triton: backend/tests/test_sources.py::test_source_shape_is_stable` |
| C-15 | The chip row stays **always visible and non-collapsible** in ERPNext, or the change is declared — it is not a dropdown and never was | `triton_widget.js:1085-1106`; contrast `triton:frontend/src/views/ChatView.vue:363-381` | 3 | `e2e/triton_sources.spec.ts::chips_are_visible_without_interaction` |

### 14.3 Everything else

| # | Behaviour that must survive | Evidence | Phase that risks breaking it | Named regression test |
|---|---|---|---|---|
| G-1 | The widget never builds for Guest or when `get_config().enabled` is false — no hidden DOM either | `triton_widget.js:1386`, `:1395` | 3 | `e2e/triton_gate.spec.ts::no_fab_for_guest_or_disabled` |
| G-2 | Whitelist off ⇒ everyone; on ⇒ Administrator + listed users only | `triton_chat.py:89-104` | 1, 6 | `test_triton_chat_proxy.py::test_user_has_widget_access_matrix` |
| G-3 | Server-side whitelist enforcement inside `mint_user_token` — bypassing the UI does not help | `triton_chat.py:121-122` | 1, 6 | `test_triton_chat_proxy.py::test_non_whitelisted_user_cannot_mint_a_token` |
| G-4 | `get_config` never returns the gateway secret and degrades to `{"enabled": False}` | `triton_chat.py:244`, `:246-248` | 1 | `test_triton_chat_proxy.py::test_get_config_never_leaks_the_secret` |
| G-5 | The widget builds exactly once across SPA route changes | `triton_widget.js:1383-1385` | 3 | `e2e/triton_mount.spec.ts::single_fab_after_ten_route_changes` |
| G-6 | Both boot triggers work (`app_ready` and the 1.5 s fallback), and a `get_config` throw allows a retry | `triton_widget.js:1391-1393`, `:1401-1403` | 3 | `e2e/triton_mount.spec.ts::mounts_when_app_ready_already_fired` |
| G-7 | `Alt+T` toggles from anywhere on the page | `triton_widget.js:299-304` | 3 | `e2e/triton_keyboard.spec.ts::alt_t_toggles_panel` |
| G-8 | Enter sends, Shift+Enter newlines | `triton_widget.js:291-296` | 3 | `e2e/triton_keyboard.spec.ts::enter_sends_shift_enter_newlines` |
| G-9 | Focus moves to the composer on open | `triton_widget.js:752` | 3 | `e2e/triton_keyboard.spec.ts::composer_focused_on_open` |
| G-10 | The four localStorage key **names** are unchanged: `triton_session_id`, `triton_model`, `triton_persona_key`, `triton_briefing_date` | `triton_widget.js:17`, `:18`, `:22`, `:25` | 3 | `e2e/triton_persistence.spec.ts::localstorage_key_names_are_stable` |
| G-11 | A dead `triton_session_id` is dropped and the widget recovers to empty | `triton_widget.js:1180-1184`, `:690` | 3 | `e2e/triton_persistence.spec.ts::stale_session_id_self_heals` |
| G-12 | First open of the day shows the briefing; a failed briefing does not burn the day's slot | `triton_widget.js:715-743` | 3, 5 | `e2e/triton_briefing.spec.ts::briefing_once_per_day_and_retries_on_failure` |
| G-13 | Context chips clear after one non-hidden turn; hidden turns send `"[]"` | `triton_widget.js:1255-1258`, `:1280` | 3, 5 | `e2e/triton_context.spec.ts::chips_consumed_after_one_turn` |
| G-14 | All four `detectPageContext` route shapes produce the documented refs, and each desk-internals read stays inside its `try/catch` | `triton_widget.js:765-806` | 3 | `e2e/triton_context.spec.ts::detects_form_list_report_and_page` |
| G-15 | The `[ERPNEXT PAGE CONTEXT]` preamble text and per-type lines are byte-stable | `triton_chat.py:210-236` | 5 | `test_triton_chat_proxy.py::test_context_preamble_is_byte_stable` |
| G-16 | Malformed `context` JSON degrades to no preamble rather than throwing | `triton_chat.py:204-206` | 5 | `test_triton_chat_proxy.py::test_malformed_context_is_ignored` |
| G-17 | The `frappe.markdown` sanitizer policy holds: allowed tags, `MD_SAFE_URL`, forced `target=_blank rel="noopener noreferrer"` on non-`#` links, `class` kept on `code`/`pre` | `frappe:frappe/public/js/frappe/utils/tools.js:38-106` | 5 | `e2e/triton_markdown.spec.ts::sanitizer_policy_snapshot` — assert the rendered HTML of a fixed adversarial markdown fixture |
| G-18 | `code.language-mermaid` survives sanitisation, so mermaid still finds its blocks | `triton_widget.js:97`; `frappe:.../tools.js:90` | 5 | `e2e/triton_markdown.spec.ts::mermaid_class_survives_sanitizer` |
| G-19 | Assistant HTML is only ever produced via `md()`; every other surface uses `esc`/`textContent` (§6.3) | §6.3 table | 3, 5 | `e2e/triton_markdown.spec.ts::no_unsanitised_innerhtml_paths` |
| G-20 | **Exactly one Vue runtime is present on a desk page** | `hooks.py:49`; `public/js/vue.global.js:18411` | **3** | `e2e/triton_vue.spec.ts::exactly_one_vue_runtime_on_the_page` — assert `window.Vue.version === "3.5.26"` and that no second copy registered |
| G-21 | `window.Vue` is neither clobbered nor shadowed by the new bundle | `public/js/erpnext_enhancements.bundle.js:17-23` | **3** | `e2e/triton_vue.spec.ts::window_vue_identity_is_stable_after_spa_mount` |
| G-22 | Panel z-index stays below dialogs and above the desk sidebar/menu | `triton_widget.css:27`, `:60` vs `frappe:.../menu.scss:15` | 3 | `e2e/triton_layering.spec.ts::persona_dialog_renders_above_panel` |
| G-23 | The 480 px breakpoint goes near-fullscreen and hides the title | `triton_widget.css:559-578` | 3 | `e2e/triton_responsive.spec.ts::mobile_panel_is_fullscreen_and_titleless` |
| G-24 | Model precedence: pick → saved → configured default → `gemini-3.5-flash` → first | `triton_widget.js:340-354` | 3 | `e2e/triton_model.spec.ts::model_selection_precedence` |
| G-25 | Persona precedence and the drop-if-missing rule | `triton_widget.js:419-431` | 3 | `e2e/triton_persona.spec.ts::deleted_persona_is_dropped_not_pinned` |
| G-26 | The `⚙ Manage…` sentinel opens the panel and does not become the selection | `triton_widget.js:449-455` | 3 | `e2e/triton_persona.spec.ts::manage_sentinel_is_not_a_selection` |
| G-27 | Per-user persona cache key + invalidation on all five mutations | `triton_chat.py:348-368` | 5, 6 | **existing** `test_triton_personas.py::test_persona_cache_key_is_per_user`, `::test_one_users_cached_personas_are_not_served_to_another`, `::test_every_mutation_invalidates_the_users_cache` — **wire into CI** |
| G-28 | `_custom_persona_path` rejects non-integers (path-traversal guard) | `triton_chat.py:371-384` | 5 | **existing** `test_triton_personas.py::test_custom_persona_path_rejects_non_integers` — **wire into CI** |
| G-29 | Approve fires the hidden continuation `"The proposed action was approved. Please proceed."` with `hidden: 1` | `triton_widget.js:1150-1153` | 3, 5 | `e2e/triton_actions.spec.ts::approval_fires_hidden_continuation` |
| G-30 | `ui_metadata.system_note` turns stay hidden from the transcript | `triton_widget.js:1190`; `triton:.../streaming.py:265` | 3, 5 | `e2e/triton_actions.spec.ts::hidden_continuation_never_appears_in_transcript` |
| G-31 | High-risk action cards get the red left border | `triton_widget.js:1111`; `triton_widget.css:379-381` | 3 | `e2e/triton_actions.spec.ts::high_risk_card_is_visually_distinct` |
| G-32 | Chart.js → frappe-charts translation, including the type map and the `<table>` fallback | `triton_widget.js:131-193` | 3, 5 | `test_triton_widget_charts.py::test_chartjs_config_maps_to_frappe_charts` |
| G-33 | `voice_dial` / `show_native_plan_approval` remain unhandled in the widget | `triton_widget.js:1354-1355` | 5 | `test_triton_widget_stream.py::test_desk_only_ui_commands_are_ignored` |
| G-34 | History limit stays 50 and replays thinking / sources / chart / pending actions | `triton_widget.js:1170`, `:1188-1205` | 3, 5 | `e2e/triton_history.spec.ts::reopened_session_replays_all_metadata` |
| G-35 | `list_models` falls back to the curated `TRITON_MODELS` when Triton is unreachable, and accepts the live list only when it has more than one entry | `triton_chat.py:330-339` | 5 | `test_triton_chat_proxy.py::test_list_models_falls_back_to_curated` |
| G-36 | `_request` retries exactly once on 401 with a force-refreshed token | `triton_chat.py:169-184` | 5 | `test_triton_chat_proxy.py::test_request_retries_once_on_401` |
| G-37 | The bridge token is cached per user under `triton_user_token::{user}` with a 120 s refresh margin | `triton_chat.py:124`, `:154`, `:31` | 5 | `test_triton_chat_proxy.py::test_bridge_token_cache_key_and_margin` |
| G-38 | Non-domain emails are rejected 403 by the bridge | `triton:.../erpnext_bridge.py:100-106` | 1, 5 | `triton: backend/tests/test_erpnext_bridge.py::test_rejects_non_domain_email` |
| G-39 | The widget CSS is imported **first** in `desk_addons.bundle.scss` (cascade order) | `public/css/desk_addons.bundle.scss:23` | 3 | `test_asset_bundles.py::test_triton_css_is_first_in_desk_addons` |
| G-40 | Both assets ship as content-hashed bundles, never raw `/assets` paths (ADR 0008) | `hooks.py:30-59` | 3 | extend the existing pattern — `test_hooks_integrity.py::test_global_assets_are_bundles` |

---

## 15. Gaps that could not be closed from code

Each is carried forward as a `VERIFY:` with its settlement method and what it blocks. None was
settled by any sibling Phase 0 note.

- **V-1.** `VERIFY: production Frappe is at or after commit 3d74400e9a, so frappe.markdown
  sanitizes its output` — settle with `bench version --format json` on the prod bench, or read
  `apps/frappe` HEAD on the deployed host. **Blocks:** the entire inline-citation sanitisation
  design (§6.2). **If prod predates it, this is not a design input — it is a live XSS exposure
  today**, because unsanitized model and tool output is written into `innerHTML` every frame
  (`triton_widget.js:961`).
- **V-2.** `VERIFY: the Bootstrap $zindex-modal / $zindex-modal-backdrop values Frappe v16
  compiles with` — asserted as 1050/1040 by convention, not read (`node_modules/bootstrap` is not
  in the local frappe checkout). Settle by opening a desk page with a dialog open and reading
  `getComputedStyle(document.querySelector('.modal')).zIndex` and the same for `.modal-backdrop`.
  **Blocks:** whether `.triton-fab` at `1040` ties with a `1040` backdrop and loses only on DOM
  order (§1.5).
- **V-3.** `VERIFY: the production ingress does not add a CSP script-src that blocks
  cdn.jsdelivr.net` — settle by reading the response headers on a prod desk page. **Blocks:**
  mermaid rendering (defect D-10); also decides whether the SPA may load anything off-origin.
- **V-4.** `VERIFY: the live GCLB backend-service timeout on production-glb-production-vm-backend`
  — settle read-only with
  `gcloud compute backend-services describe production-glb-production-vm-backend --global
  --project=erpnext-465317 --format="yaml(name,timeoutSec)"`. Terraform never sets it
  (`infra/configs/load_balancer.yaml:33-39`), so the value is GCP's 30 s default **unless someone
  raised it out-of-band — in which case a `terraform apply` would silently revert it.**
  **Blocks:** whether any streaming design may rely on a budget longer than 30 s (defect D-9).
- **V-5.** `VERIFY: that {FRAPPE_BASE_URL}/desk/{slug}/{name} resolves on the production Frappe
  v16 site` — settle by opening `{site}/desk/task/TASK-0001` in a browser. **Blocks:** every
  ERPNext source chip link, and any inline citation built on the same URL builder
  (`triton:.../sources.py:24-39`). If it 404s or redirects, the chips are **already** dead links.
- **V-6.** `VERIFY: whether Triton prunes ChatMessage / ChatSession rows on any schedule` —
  settle by reading `triton:backend/app/models/chat.py` and the scheduler modules under
  `triton:backend/app/core/`. **Blocks:** any statement about chat history retention, and the
  privacy answer for data already egressed to Triton (§12).
- **V-7.** `VERIFY: no other installed app on the production bench ships a second window.Vue` —
  settle on a prod desk page with `window.Vue.version`, plus a grep of the deployed
  `sites/assets/` for `vue.global`. **Blocks:** the §11 coexistence recommendation; a third copy
  from ERPNext core or another app changes which option is correct.
- **V-8.** `VERIFY: whether enable_write_actions gates anything at all, in either repo` — a
  repo-wide grep finds it only at `triton_chat.py:77`, `:255` and in the doctype JSON; settle by
  grepping the Triton repo for any consumption and by checking whether Triton receives it through
  another channel. **Blocks:** whether the Settings checkbox is kept, wired up, or removed
  (defect D-3). If nothing reads it, the checkbox misinforms the administrator about a write
  control.

---

*Appendix A ends. Appendix B (`0009-appendix-b-implementation-plan.md`) sequences the work; the
ADR itself (`0009-erpnext-google-chat-triton.md`) carries the decisions and the open `CQ-n`
questions for the human.*
